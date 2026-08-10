//! Native implementation of Apple's web SRP sign-in and setup-session flow.
//! Only the OS credential vault may persist credentials or session tokens.
//!
//! Derived key material (the PBKDF2 output, the SRP session key, the private
//! exponent) is held in `Zeroizing` and wiped on drop. The caller's plaintext
//! password is not: it also lives in the parsed request `Value` and in the raw
//! stdin line for the life of the request, so treat process memory as holding
//! the password until the request completes rather than assuming it is wiped.

use std::collections::BTreeMap;
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::STANDARD as B64;
use num_bigint::BigUint;
use pbkdf2::pbkdf2_hmac;
use rand::RngCore;
use rand::rngs::OsRng;
use reqwest::header::{
    ACCEPT, CONTENT_TYPE, HeaderMap, HeaderName, HeaderValue, ORIGIN, REFERER, USER_AGENT,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::error::{AppError, Result};

const IDMSA: &str = "https://idmsa.apple.com/appleauth/auth";
const SETUP: &str = "https://setup.icloud.com/setup/ws/1";
const WIDGET_KEY: &str =
    "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d";
const CLIENT_BUILD_NUMBER: &str = "2534Project66";
const CLIENT_MASTERING_NUMBER: &str = "2534B22";
const CKJS_BUILD_VERSION: &str = "17DProjectDev77";
const MAX_APPLE_RESPONSE_BYTES: usize = 2 * 1024 * 1024;
const USER_AGENT_VALUE: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15";
const FD_CLIENT_INFO: &str = r#"{"U":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15","L":"en-US","Z":"GMT+00:00","V":"1.1","F":""}"#;

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct SessionState {
    pub client_id: String,
    pub session_id: Option<String>,
    pub session_token: Option<String>,
    pub scnt: Option<String>,
    #[serde(default)]
    pub auth_attributes: Option<String>,
    pub account_country: Option<String>,
    pub trust_token: Option<String>,
    pub dsid: Option<String>,
    pub webservices: BTreeMap<String, Service>,
    #[serde(default)]
    pub hsa_version: i64,
    #[serde(default)]
    pub trusted_session: bool,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Service {
    pub url: String,
    #[serde(default)]
    pub status: String,
}

#[derive(Debug, Deserialize)]
struct SrpChallenge {
    #[serde(rename = "b")]
    server_public: String,
    #[serde(rename = "c")]
    challenge: String,
    #[serde(rename = "iteration")]
    iterations: u32,
    protocol: String,
    salt: String,
}

struct SrpProof {
    m1: Vec<u8>,
    m2: Vec<u8>,
}

pub struct AuthClient {
    http: reqwest::Client,
    pub state: SessionState,
}

impl AuthClient {
    pub fn new(state: Option<SessionState>) -> Result<Self> {
        let mut state = state.unwrap_or_default();
        if state.client_id.is_empty() {
            state.client_id = Uuid::new_v4().to_string().to_lowercase();
        }
        let http = reqwest::Client::builder()
            .https_only(true)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .cookie_store(true)
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(60))
            .user_agent(USER_AGENT_VALUE)
            .build()?;
        Ok(Self { http, state })
    }

    pub fn http(&self) -> &reqwest::Client {
        &self.http
    }

    pub fn query(&self) -> Vec<(&'static str, String)> {
        let mut query = vec![
            ("clientBuildNumber", CLIENT_BUILD_NUMBER.into()),
            ("clientMasteringNumber", CLIENT_MASTERING_NUMBER.into()),
            ("ckjsBuildVersion", CKJS_BUILD_VERSION.into()),
            ("clientId", self.state.client_id.clone()),
        ];
        if let Some(dsid) = &self.state.dsid {
            query.push(("dsid", dsid.clone()));
        }
        query
    }

    pub async fn sign_in(
        &mut self,
        apple_id: &str,
        password: &str,
        accept_terms: bool,
    ) -> Result<Value> {
        // iCloud's web login first establishes the browser-auth context and
        // cookies used by the two SRP requests. The Python implementation this
        // connector replaced performs the same bootstrap.
        let auth_headers = self.idmsa_headers()?;
        let bootstrap = self
            .http
            .get(format!("{IDMSA}/authorize/signin"))
            .headers(auth_headers)
            .query(&[
                ("frame_id", self.state.client_id.as_str()),
                ("skVersion", "7"),
                ("iframeid", self.state.client_id.as_str()),
                ("client_id", WIDGET_KEY),
                ("response_type", "code"),
                ("redirect_uri", "https://www.icloud.com"),
                ("response_mode", "web_message"),
                ("state", self.state.client_id.as_str()),
                ("authVersion", "latest"),
            ])
            .send()
            .await?;
        self.capture_headers(bootstrap.headers());
        if !bootstrap.status().is_success() {
            let status = bootstrap.status().as_u16();
            let body = bounded_response_text(bootstrap).await?;
            return Err(classify_auth(status, &body));
        }

        let private = random_private();
        let n = modulus()?;
        let public = BigUint::from(2u8).modpow(&private, &n);
        let public_bytes = public.to_bytes_be();

        let init = self
            .http
            .post(format!("{IDMSA}/signin/init"))
            .headers(self.idmsa_headers()?)
            .query(&[("isRememberMeEnabled", "true")])
            .json(&json!({
                "accountName": apple_id,
                "a": B64.encode(&public_bytes),
                "protocols": ["s2k", "s2k_fo"]
            }))
            .send()
            .await?;
        self.capture_headers(init.headers());
        let status = init.status();
        let body = bounded_response_text(init).await?;
        if !status.is_success() {
            return Err(classify_auth(status.as_u16(), &body));
        }
        let challenge: SrpChallenge = serde_json::from_str(&body)?;
        let proof = make_proof(apple_id, password.as_bytes(), private, &challenge)?;

        let complete = self
            .http
            .post(format!("{IDMSA}/signin/complete"))
            .headers(self.idmsa_headers()?)
            .query(&[("isRememberMeEnabled", "true")])
            .json(&json!({
                "accountName": apple_id,
                "c": challenge.challenge,
                "m1": B64.encode(proof.m1),
                "m2": B64.encode(proof.m2),
                "rememberMe": true,
                "trustTokens": self.state.trust_token.iter().collect::<Vec<_>>()
            }))
            .send()
            .await?;
        self.capture_headers(complete.headers());
        let status = complete.status();
        let body = bounded_response_text(complete).await?;
        if !status.is_success() {
            return Err(classify_auth(status.as_u16(), &body));
        }
        let response: Value = if body.trim().is_empty() {
            json!({})
        } else {
            serde_json::from_str(&body)?
        };
        match self.account_login().await {
            Err(AppError::TermsRequired { .. }) if accept_terms => {
                let accepted = self
                    .http
                    .post(format!("{SETUP}/acceptTermsOfService"))
                    .query(&self.query())
                    .headers(self.setup_headers()?)
                    .json(&json!({}))
                    .send()
                    .await?;
                if !accepted.status().is_success() {
                    return Err(AppError::TermsRequired {
                        message: "Apple did not accept the updated iCloud terms.".into(),
                        detail: format!("HTTP {}", accepted.status().as_u16()),
                    });
                }
                self.account_login().await?;
            }
            other => other?,
        }
        Ok(response)
    }

    async fn account_login(&mut self) -> Result<()> {
        let token = self
            .state
            .session_token
            .clone()
            .ok_or_else(|| AppError::AuthRequired {
                message: "Apple did not issue a web authentication token".into(),
                detail: String::new(),
            })?;
        let country = self
            .state
            .account_country
            .clone()
            .unwrap_or_else(|| "USA".into());
        let mut body = json!({
            "accountCountryCode": country,
            "dsWebAuthToken": token,
            "extended_login": true
        });
        if let Some(trust) = &self.state.trust_token {
            body["trustToken"] = Value::String(trust.clone());
        }
        let response = self
            .http
            .post(format!("{SETUP}/accountLogin"))
            .query(&self.query())
            .headers(self.setup_headers()?)
            .json(&body)
            .send()
            .await?;
        let status = response.status();
        let text = bounded_response_text(response).await?;
        if !status.is_success() {
            return Err(classify_auth(status.as_u16(), &text));
        }
        let value: Value = serde_json::from_str(&text)?;
        self.state.dsid = value
            .pointer("/dsInfo/dsid")
            .and_then(Value::as_str)
            .map(str::to_owned);
        self.state.hsa_version = value
            .pointer("/dsInfo/hsaVersion")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        self.state.trusted_session = value
            .pointer("/hsaTrustedBrowser")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        self.state.webservices =
            serde_json::from_value(value.get("webservices").cloned().unwrap_or(json!({})))
                .unwrap_or_default();
        if value.get("termsUpdateNeeded").and_then(Value::as_bool) == Some(true) {
            return Err(AppError::TermsRequired {
                message: "Apple requires you to accept updated iCloud terms.".into(),
                detail: safe_detail(&text),
            });
        }
        Ok(())
    }

    pub async fn request_2fa(&self) -> Result<bool> {
        let response = self
            .http
            .get(format!("{IDMSA}/verify/trusteddevice"))
            .headers(self.idmsa_headers()?)
            .send()
            .await?;
        Ok(response.status().is_success())
    }

    pub async fn submit_2fa(&mut self, code: &str) -> Result<()> {
        if code.len() != 6 || !code.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(AppError::bad_request(
                "The verification code must contain six digits",
            ));
        }
        let response = self
            .http
            .post(format!("{IDMSA}/verify/trusteddevice/securitycode"))
            .headers(self.idmsa_headers()?)
            .json(&json!({"securityCode":{"code":code}}))
            .send()
            .await?;
        let status = response.status();
        let text = bounded_response_text(response).await?;
        if !status.is_success() {
            return Err(AppError::TwoFactorRequired {
                message: "That code was rejected".into(),
                detail: text,
            });
        }
        let trust = self
            .http
            .get(format!("{IDMSA}/2sv/trust"))
            .headers(self.idmsa_headers()?)
            .send()
            .await?;
        self.capture_headers(trust.headers());
        self.state.trusted_session = trust.status().is_success();
        self.account_login().await
    }

    fn idmsa_headers(&self) -> Result<HeaderMap> {
        let mut headers = HeaderMap::new();
        headers.insert(
            ACCEPT,
            HeaderValue::from_static("application/json, text/javascript"),
        );
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(ORIGIN, HeaderValue::from_static("https://www.icloud.com"));
        headers.insert(REFERER, HeaderValue::from_static(IDMSA));
        headers.insert(USER_AGENT, HeaderValue::from_static(USER_AGENT_VALUE));
        for (name, value) in [
            ("x-apple-oauth-client-id", WIDGET_KEY),
            ("x-apple-oauth-client-type", "firstPartyAuth"),
            ("x-apple-oauth-redirect-uri", "https://www.icloud.com"),
            ("x-apple-oauth-require-grant-code", "true"),
            ("x-apple-oauth-response-mode", "web_message"),
            ("x-apple-oauth-response-type", "code"),
            ("x-apple-widget-key", WIDGET_KEY),
            ("x-apple-fd-client-info", FD_CLIENT_INFO),
        ] {
            headers.insert(
                HeaderName::from_static(name),
                HeaderValue::from_static(value),
            );
        }
        insert(
            &mut headers,
            "x-apple-oauth-state",
            &self.state.client_id,
        )?;
        insert(&mut headers, "x-apple-frame-id", &self.state.client_id)?;
        if let Some(attributes) = &self.state.auth_attributes {
            insert(&mut headers, "x-apple-auth-attributes", attributes)?;
        }
        if let Some(session_id) = &self.state.session_id {
            insert(&mut headers, "x-apple-id-session-id", session_id)?;
        }
        if let Some(scnt) = &self.state.scnt {
            insert(&mut headers, "scnt", scnt)?;
        }
        if let Some(token) = &self.state.session_token {
            insert(&mut headers, "x-apple-webauth-token", token)?;
        }
        if let Some(country) = &self.state.account_country {
            insert(&mut headers, "x-apple-id-account-country", country)?;
        }
        Ok(headers)
    }

    pub fn setup_headers(&self) -> Result<HeaderMap> {
        let mut headers = HeaderMap::new();
        headers.insert(ORIGIN, HeaderValue::from_static("https://www.icloud.com"));
        headers.insert(REFERER, HeaderValue::from_static("https://www.icloud.com/"));
        if let Some(session_id) = &self.state.session_id {
            insert(&mut headers, "x-apple-id-session-id", session_id)?;
        }
        if let Some(scnt) = &self.state.scnt {
            insert(&mut headers, "scnt", scnt)?;
        }
        if let Some(token) = &self.state.session_token {
            insert(&mut headers, "x-apple-webauth-token", token)?;
        }
        if let Some(country) = &self.state.account_country {
            insert(&mut headers, "x-apple-id-account-country", country)?;
        }
        Ok(headers)
    }

    fn capture_headers(&mut self, headers: &HeaderMap) {
        self.state.session_id =
            header(headers, "x-apple-id-session-id").or(self.state.session_id.take());
        self.state.session_token =
            header(headers, "x-apple-session-token").or(self.state.session_token.take());
        self.state.scnt = header(headers, "scnt").or(self.state.scnt.take());
        self.state.auth_attributes =
            header(headers, "x-apple-auth-attributes").or(self.state.auth_attributes.take());
        self.state.account_country =
            header(headers, "x-apple-id-account-country").or(self.state.account_country.take());
        self.state.trust_token =
            header(headers, "x-apple-twosv-trust-token").or(self.state.trust_token.take());
    }
}

fn modulus() -> Result<BigUint> {
    Ok(srp::groups::G_2048.n.clone())
}

fn random_private() -> BigUint {
    let mut bytes = Zeroizing::new([0u8; 32]);
    OsRng.fill_bytes(bytes.as_mut());
    BigUint::from_bytes_be(bytes.as_ref())
}

fn hash(parts: &[&[u8]]) -> Vec<u8> {
    let mut digest = Sha256::new();
    for part in parts {
        digest.update(part);
    }
    digest.finalize().to_vec()
}

fn pad(value: &BigUint, length: usize) -> Vec<u8> {
    let bytes = value.to_bytes_be();
    let mut output = vec![0; length.saturating_sub(bytes.len())];
    output.extend_from_slice(&bytes);
    output
}

fn lower_hex(bytes: &[u8]) -> Vec<u8> {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = Vec::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize]);
        output.push(HEX[(byte & 0x0f) as usize]);
    }
    output
}

fn derive_password(
    password: &[u8],
    salt: &[u8],
    iterations: u32,
    protocol: &str,
) -> Zeroizing<[u8; 32]> {
    // Apple's s2k variants always start with SHA-256(password). The fallback
    // variant feeds the lowercase hexadecimal form of that digest to PBKDF2.
    let password_hash = Zeroizing::new(hash(&[password]));
    let filtered = Zeroizing::new(if protocol == "s2k_fo" {
        lower_hex(password_hash.as_slice())
    } else {
        password_hash.to_vec()
    });
    let mut derived = Zeroizing::new([0u8; 32]);
    pbkdf2_hmac::<Sha256>(filtered.as_slice(), salt, iterations, derived.as_mut());
    derived
}

fn make_proof(
    username: &str,
    password: &[u8],
    private: BigUint,
    challenge: &SrpChallenge,
) -> Result<SrpProof> {
    let n = modulus()?;
    let width = n.to_bytes_be().len();
    if !matches!(challenge.protocol.as_str(), "s2k" | "s2k_fo") {
        return Err(AppError::AuthRequired {
            message: "iCloud selected an unsupported sign-in protocol".into(),
            detail: String::new(),
        });
    }
    if !(1..=1_000_000).contains(&challenge.iterations) {
        return Err(AppError::AuthRequired {
            message: "iCloud returned invalid sign-in parameters".into(),
            detail: String::new(),
        });
    }
    let g = BigUint::from(2u8);
    let a_pub = g.modpow(&private, &n);
    let b_bytes = B64
        .decode(&challenge.server_public)
        .map_err(|e| AppError::bad_request(format!("invalid SRP challenge: {e}")))?;
    if b_bytes.is_empty() || b_bytes.len() > width {
        return Err(AppError::AuthRequired {
            message: "iCloud returned an invalid sign-in challenge".into(),
            detail: String::new(),
        });
    }
    let b_pub = BigUint::from_bytes_be(&b_bytes);
    if (&b_pub % &n) == BigUint::default() {
        return Err(AppError::AuthRequired {
            message: "iCloud returned an invalid sign-in challenge".into(),
            detail: String::new(),
        });
    }
    // KNOWN DIVERGENCE, needs a live account to settle (see
    // docs/srp-reference-vectors.md). pyicloud's SRP backend routes the salt
    // through an OpenSSL BIGNUM in both `gen_x` and `calculate_M`, which strips
    // leading zero bytes. We hash the salt verbatim. The two agree for every
    // salt whose first byte is non-zero -- which the vectors below cover -- and
    // disagree for roughly one account in 256. Because the salt is fixed when
    // the password is set, an affected account would fail sign-in every time
    // rather than intermittently. Do not "fix" this by guessing: confirm
    // against a disposable account whose salt starts with 0x00.
    let salt = B64
        .decode(&challenge.salt)
        .map_err(|e| AppError::bad_request(format!("invalid SRP salt: {e}")))?;
    if salt.is_empty() || salt.len() > 64 {
        return Err(AppError::AuthRequired {
            message: "iCloud returned invalid sign-in parameters".into(),
            detail: String::new(),
        });
    }

    let derived = derive_password(
        password,
        &salt,
        challenge.iterations,
        &challenge.protocol,
    );
    // Apple's GSA mode deliberately excludes the username from x. It retains
    // the separator byte, so x = H(salt || H(":" || derived password)).
    let identity_hash = Zeroizing::new(hash(&[b":", derived.as_ref()]));
    let x = BigUint::from_bytes_be(&hash(&[&salt, &identity_hash]));
    let n_pad = pad(&n, width);
    let g_pad = pad(&g, width);
    let a_pad = pad(&a_pub, width);
    let b_pad = pad(&b_pub, width);
    let k = BigUint::from_bytes_be(&hash(&[&n_pad, &g_pad]));
    let u = BigUint::from_bytes_be(&hash(&[&a_pad, &b_pad]));
    // SRP-6a requires aborting when u == 0; continuing would drop the server
    // public value out of the shared-secret exponent entirely.
    if u == BigUint::default() {
        return Err(AppError::AuthRequired {
            message: "iCloud returned an invalid sign-in challenge".into(),
            detail: String::new(),
        });
    }
    let gx = g.modpow(&x, &n);
    let base = (&b_pub + &n - ((&k * gx) % &n)) % &n;
    let exponent = private + (&u * &x);
    let shared = base.modpow(&exponent, &n);
    let session_key = Zeroizing::new(hash(&[&shared.to_bytes_be()]));
    let h_n = hash(&[&n_pad]);
    let h_g = hash(&[&g_pad]);
    let xor: Vec<u8> = h_n.iter().zip(h_g.iter()).map(|(a, b)| a ^ b).collect();
    let h_user = hash(&[username.as_bytes()]);
    let a_bytes = a_pub.to_bytes_be();
    let b_bytes = b_pub.to_bytes_be();
    let m1 = hash(&[&xor, &h_user, &salt, &a_bytes, &b_bytes, &session_key]);
    let m2 = hash(&[&a_bytes, &m1, &session_key]);
    Ok(SrpProof { m1, m2 })
}

fn insert(headers: &mut HeaderMap, name: &'static str, value: &str) -> Result<()> {
    let value = HeaderValue::from_str(value)
        .map_err(|e| AppError::internal("Invalid authentication header", e.to_string()))?;
    headers.insert(HeaderName::from_static(name), value);
    Ok(())
}

fn header(headers: &HeaderMap, name: &'static str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

fn classify_auth(status: u16, body: &str) -> AppError {
    let detail = safe_detail(body);
    if body.contains("termsUpdateNeeded") {
        return AppError::TermsRequired {
            message: "Apple requires you to accept updated iCloud terms.".into(),
            detail,
        };
    }
    // Match on parsed fields rather than searching the whole body: an
    // unrelated error whose text merely mentions "verification" would
    // otherwise push the user into a 2FA prompt they cannot satisfy.
    let parsed: Option<Value> = serde_json::from_str(body).ok();
    let two_factor = status == 409
        || parsed.as_ref().is_some_and(|value| {
            let field = |pointer: &str| {
                value
                    .pointer(pointer)
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_ascii_lowercase()
            };
            field("/authType").contains("hsa")
                || field("/serviceErrors/0/code") == "hsa2"
                || value
                    .pointer("/hsaChallengeRequired")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
        });
    if two_factor {
        return AppError::TwoFactorRequired {
            message: "Two-factor authentication required".into(),
            detail,
        };
    }
    if status == 401 || status == 403 || status == 421 {
        return AppError::AuthRequired {
            message: "iCloud rejected the sign-in".into(),
            detail,
        };
    }
    AppError::Network {
        message: "iCloud sign-in failed".into(),
        detail: format!("HTTP {status}: {detail}"),
    }
}

fn safe_detail(body: &str) -> String {
    let Ok(value) = serde_json::from_str::<Value>(body) else {
        return "Apple returned an unstructured error".into();
    };
    for pointer in [
        "/errorMessage",
        "/reason",
        "/serviceErrors/0/message",
        "/serviceErrors/0/code",
    ] {
        if let Some(text) = value.pointer(pointer).and_then(Value::as_str) {
            return text.chars().take(300).collect();
        }
    }
    "Apple returned an error without a public reason".into()
}

pub(crate) async fn bounded_response_text(mut response: reqwest::Response) -> Result<String> {
    if response
        .content_length()
        .is_some_and(|size| size > MAX_APPLE_RESPONSE_BYTES.try_into().unwrap_or(u64::MAX))
    {
        return Err(AppError::Network {
            message: "iCloud returned an unexpectedly large response".into(),
            detail: String::new(),
        });
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if body.len().saturating_add(chunk.len()) > MAX_APPLE_RESPONSE_BYTES {
            return Err(AppError::Network {
                message: "iCloud returned an unexpectedly large response".into(),
                detail: String::new(),
            });
        }
        body.extend_from_slice(&chunk);
    }
    String::from_utf8(body)
        .map_err(|e| AppError::internal("iCloud returned invalid text", e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::{SrpChallenge, derive_password, hash, lower_hex, make_proof};
    use base64::Engine;
    use base64::engine::general_purpose::STANDARD as B64;
    use num_bigint::BigUint;

    fn hex(bytes: &[u8]) -> String {
        String::from_utf8(lower_hex(bytes)).expect("hex is ASCII")
    }

    // Reference values produced by the stack the deleted Python sidecar used:
    // pyicloud 2.6.5's SrpPassword together with srp 1.0.22, driven with
    // `srp.rfc5054_enable()` and `srp.no_username_in_x()` exactly as
    // pyicloud.base does. Regenerate with the script in
    // docs/srp-reference-vectors.md if Apple's parameters ever change.
    const VECTOR_USER: &str = "vector@example.com";
    const VECTOR_PASSWORD: &[u8] = b"correct horse battery staple";
    const VECTOR_SALT_B64: &str = "AQIDBAUGBwgJCgsMDQ4PEA==";
    const VECTOR_B_B64: &str = concat!(
        "16nx1fXiKf9ErlKozGDyuLLTNIjPBJAf5qOPi45DFvjXqfHV9eIp/0SuUqjMYPK4stM0iM8EkB/mo4+L",
        "jkMW+Nep8dX14in/RK5SqMxg8riy0zSIzwSQH+ajj4uOQxb416nx1fXiKf9ErlKozGDyuLLTNIjPBJAf",
        "5qOPi45DFvjXqfHV9eIp/0SuUqjMYPK4stM0iM8EkB/mo4+LjkMW+Nep8dX14in/RK5SqMxg8riy0zSI",
        "zwSQH+ajj4uOQxb416nx1fXiKf9ErlKozGDyuLLTNIjPBJAf5qOPi45DFvjXqfHV9eIp/0SuUqjMYPK4",
        "stM0iM8EkB/mo4+LjkMW+A=="
    );

    fn vector_challenge(protocol: &str) -> SrpChallenge {
        SrpChallenge {
            server_public: VECTOR_B_B64.into(),
            challenge: "c".into(),
            iterations: 1000,
            protocol: protocol.into(),
            salt: VECTOR_SALT_B64.into(),
        }
    }

    /// The private exponent pyicloud passed as `bytes_a` when the vectors
    /// below were generated.
    fn vector_private() -> BigUint {
        BigUint::from_bytes_be(&(1u8..=32).collect::<Vec<u8>>())
    }

    #[test]
    fn srp_proof_matches_pyicloud_reference_vectors() {
        for (protocol, m1, m2) in [
            (
                "s2k",
                "65d959dcd99bb14ebe4cdf8d57fee4fc15fd3375df8f06f8a7fd73ea83bc933f",
                "02927bdb75a3a2322e789cc4d0c7e90d3b8c08c449ccf9d380e9dcc19ea6fb92",
            ),
            (
                "s2k_fo",
                "082a47581fdec08156bc4135d3d8d171920c3126cf307f966d459ea5043bafdf",
                "add0f31339cc14e4618d8d47ee1032c23897122b475b600046d96aaf7cf45579",
            ),
        ] {
            let proof = make_proof(
                VECTOR_USER,
                VECTOR_PASSWORD,
                vector_private(),
                &vector_challenge(protocol),
            )
            .expect("the reference challenge is well formed");
            assert_eq!(hex(&proof.m1), m1, "m1 mismatch for {protocol}");
            assert_eq!(hex(&proof.m2), m2, "m2 mismatch for {protocol}");
        }
    }

    #[test]
    fn srp_proof_rejects_a_degenerate_server_public_value() {
        let mut challenge = vector_challenge("s2k");
        challenge.server_public = B64.encode(super::modulus().expect("modulus").to_bytes_be());
        assert!(
            make_proof(
                VECTOR_USER,
                VECTOR_PASSWORD,
                vector_private(),
                &challenge
            )
            .is_err(),
            "B congruent to 0 mod N must abort"
        );
    }

    #[test]
    fn srp_proof_rejects_unsupported_parameters() {
        for mutate in [
            (|c: &mut SrpChallenge| c.protocol = "s2k_unknown".into()) as fn(&mut SrpChallenge),
            |c: &mut SrpChallenge| c.iterations = 0,
            |c: &mut SrpChallenge| c.iterations = 2_000_000,
            |c: &mut SrpChallenge| c.salt = B64.encode([0u8; 65]),
            |c: &mut SrpChallenge| c.salt = B64.encode([]),
        ] {
            let mut challenge = vector_challenge("s2k");
            mutate(&mut challenge);
            assert!(
                make_proof(
                    VECTOR_USER,
                    VECTOR_PASSWORD,
                    vector_private(),
                    &challenge
                )
                .is_err(),
                "invalid sign-in parameters must be rejected"
            );
        }
    }

    #[test]
    fn apple_password_derivation_matches_pyicloud_reference_vectors() {
        let password = b"correct horse battery staple";
        let salt: Vec<u8> = (0..16).collect();

        let s2k = derive_password(password, &salt, 1000, "s2k");
        assert_eq!(
            hex(s2k.as_ref()),
            "6b7cc6edb94620dcf9811c616742ca428fe81b5bede8478a876a895345ded185"
        );

        let s2k_fo = derive_password(password, &salt, 1000, "s2k_fo");
        assert_eq!(
            hex(s2k_fo.as_ref()),
            "5df3f8614930d6e2cf855fc8ddec13b51431c6c08e3375a1ef346686965d049e"
        );

        let inner = hash(&[b":", s2k_fo.as_ref()]);
        assert_eq!(
            hex(&hash(&[&salt, &inner])),
            "42bb4a1784a729202320441ea9cb4866966552e000de056e690160dfad2266f4"
        );
    }
}
