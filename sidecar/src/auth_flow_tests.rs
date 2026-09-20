//! Exercise the HTTP sequence against a local server; no Apple account is used.
use super::*;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::thread::JoinHandle;

type Reply = (&'static str, &'static str, u16, &'static str, &'static str);

fn mock(replies: Vec<Reply>) -> (AuthClient, JoinHandle<Vec<String>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let address = listener.local_addr().unwrap();
    let task = std::thread::spawn(move || {
        let mut requests = Vec::new();
        for (method, path, status, headers, body) in replies {
            let deadline = std::time::Instant::now() + Duration::from_secs(10);
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(
                            std::time::Instant::now() < deadline,
                            "missing {method} {path}"
                        );
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("{error}"),
                }
            };
            // Windows can inherit the listener's nonblocking mode on accept.
            // Request reads must wait for bytes, bounded by the read timeout.
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut bytes = Vec::new();
            let mut byte = [0u8; 1];
            while !bytes.ends_with(b"\r\n\r\n") {
                stream.read_exact(&mut byte).unwrap();
                bytes.push(byte[0]);
                assert!(bytes.len() < 32768);
            }
            let head = String::from_utf8(bytes).unwrap();
            let first = head.lines().next().unwrap();
            assert!(first.starts_with(&format!("{method} {path}")), "{first}");
            let length = head
                .lines()
                .find_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    key.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
                .unwrap_or(0);
            let mut payload = vec![0; length];
            stream.read_exact(&mut payload).unwrap();
            requests.push(format!("{head}{}", String::from_utf8(payload).unwrap()));
            write!(stream, "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n{body}", body.len()).unwrap();
        }
        requests
    });
    let mut auth = AuthClient::new(None).unwrap();
    auth.http = reqwest::Client::builder()
        .no_proxy()
        .cookie_store(true)
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    auth.auth_endpoint = format!("http://{address}/auth");
    auth.setup_endpoint = format!("http://{address}/setup");
    (auth, task)
}

const TRUSTED: &str = r#"{"dsInfo":{"dsid":"123","hsaVersion":2},"hsaTrustedBrowser":true,"hsaChallengeRequired":false,"webservices":{}}"#;
const LIMITED: &str = r#"{"dsInfo":{"dsid":"123","hsaVersion":2},"hsaTrustedBrowser":false,"hsaChallengeRequired":true,"webservices":{}}"#;

#[tokio::test]
async fn one_sms_from_preparation_through_verification_and_trust() {
    let (mut auth, server) = mock(vec![
        (
            "GET",
            "/auth ",
            200,
            "",
            r#"{"trustedPhoneNumbers":[{"id":7,"pushMode":"sms"}]}"#,
        ),
        ("POST", "/setup/accountLogin", 200, "", LIMITED),
        (
            "PUT",
            "/auth/verify/phone ",
            200,
            "scnt: after-send\r\nx-apple-id-session-id: challenge\r\nSet-Cookie: challenge=cookie; Path=/\r\n",
            "{}",
        ),
        (
            "POST",
            "/auth/verify/phone/securitycode ",
            204,
            "scnt: after-code\r\n",
            "",
        ),
        (
            "GET",
            "/auth/2sv/trust ",
            204,
            "X-Apple-TwoSV-Trust-Token: durable-trust\r\nX-Apple-Session-Token: durable-session\r\n",
            "",
        ),
        ("POST", "/setup/accountLogin", 200, "", TRUSTED),
    ]);
    auth.state.session_token = Some("limited-token".into());
    auth.prime_two_factor().await;
    assert_eq!(auth.two_factor_status()["sent"], false);
    auth.request_2fa().await.unwrap();
    auth.submit_2fa("123456").await.unwrap();
    assert!(!auth.requires_two_factor());
    assert_eq!(auth.state.trust_token.as_deref(), Some("durable-trust"));
    let requests = server.join().unwrap();
    let verification = requests[3].to_ascii_lowercase();
    assert!(verification.contains("scnt: after-send"));
    assert!(verification.contains("cookie: challenge=cookie"));
    assert!(requests[3].contains(r#""securityCode":{"code":"123456"}"#));
    assert!(requests[4].contains("scnt: after-code"));
    assert!(requests[5].contains("durable-trust"));
}

#[tokio::test]
async fn restart_uses_saved_tokens_without_password_or_code_delivery() {
    let (mut auth, server) = mock(vec![(
        "POST",
        "/setup/accountLogin",
        200,
        "X-Apple-Session-Token: refreshed-token\r\n",
        TRUSTED,
    )]);
    let saved = SessionState {
        session_token: Some("saved-token".into()),
        trust_token: Some("saved-trust".into()),
        ..SessionState::default()
    };
    auth.state = serde_json::from_str(&serde_json::to_string(&saved).unwrap()).unwrap();
    assert!(auth.resume_session().await.unwrap());
    assert_eq!(auth.state.session_token.as_deref(), Some("refreshed-token"));
    let requests = server.join().unwrap();
    assert!(requests[0].contains(r#""dsWebAuthToken":"saved-token""#));
    assert!(requests[0].contains(r#""trustToken":"saved-trust""#));
}

#[tokio::test]
async fn restore_distinguishes_rejection_from_outage_and_incomplete_challenge() {
    for (status, body, retry) in [
        (401, "{}", false),
        (503, "{}", true),
        (200, LIMITED, false),
        (
            200,
            r#"{"dsInfo":{"hsaVersion":2},"hsaTrustedBrowser":true,"hsaChallengeRequired":true}"#,
            false,
        ),
    ] {
        let (mut auth, server) = mock(vec![("POST", "/setup/accountLogin", status, "", body)]);
        auth.state.session_token = Some("saved-token".into());
        let result = auth.resume_session().await;
        if retry {
            assert!(matches!(result, Err(AppError::Network { .. })));
        } else {
            assert!(!result.unwrap());
        }
        assert_eq!(auth.state.session_token.as_deref(), Some("saved-token"));
        server.join().unwrap();
    }
}

#[tokio::test]
async fn accepted_conflict_completes_trust_for_sms_and_device_codes() {
    for (route, path, headers, body) in [
        (
            TwoFactorRoute::Sms(2, "sms".into()),
            "/auth/verify/phone/securitycode ",
            "X-Apple-Session-Token: verified-token\r\nscnt: verified\r\n",
            "{}",
        ),
        (
            TwoFactorRoute::Sms(2, "sms".into()),
            "/auth/verify/phone/securitycode ",
            "scnt: verified\r\n",
            r#"{"securityCode":{"valid":true}}"#,
        ),
        (
            TwoFactorRoute::TrustedDevice,
            "/auth/verify/trusteddevice/securitycode ",
            "scnt: verified\r\n",
            r#"{"securityCode":{"valid":true}}"#,
        ),
    ] {
        let (mut auth, server) = mock(vec![
            ("POST", path, 409, headers, body),
            (
                "GET",
                "/auth/2sv/trust ",
                204,
                "X-Apple-TwoSV-Trust-Token: trust-token\r\n",
                "",
            ),
            ("POST", "/setup/accountLogin", 200, "", TRUSTED),
        ]);
        auth.state.session_token = Some("limited-token".into());
        auth.mark_sent(route);
        auth.submit_2fa("123456").await.unwrap();
        assert!(auth.state.trusted_session);
        let requests = server.join().unwrap();
        assert!(requests[1].contains("scnt: verified"));
        assert!(requests[2].contains("trust-token"));
        if !headers.starts_with("scnt") {
            assert!(requests[2].contains("verified-token"));
        }
    }
}

#[tokio::test]
async fn ambiguous_or_rejected_codes_never_attempt_trust() {
    for body in [
        "{}",
        r#"{"securityCode":{"valid":false}}"#,
        r#"{"securityCode":{"valid":true},"service_errors":[{"code":"-21669"}]}"#,
    ] {
        let (mut auth, server) = mock(vec![(
            "POST",
            "/auth/verify/phone/securitycode ",
            409,
            "",
            body,
        )]);
        auth.state.session_token = Some("old-token-is-not-proof".into());
        auth.mark_sent(TwoFactorRoute::Sms(2, "sms".into()));
        assert!(matches!(
            auth.submit_2fa("123456").await,
            Err(AppError::TwoFactorRequired { .. })
        ));
        assert!(!auth.state.trusted_session);
        assert_eq!(server.join().unwrap().len(), 1);
    }
}

#[tokio::test]
async fn accepted_code_is_not_a_trusted_session_until_apple_confirms() {
    for account_body in [LIMITED, "{}"] {
        let (mut auth, server) = mock(vec![
            (
                "POST",
                "/auth/verify/phone/securitycode ",
                409,
                "",
                r#"{"securityCode":{"valid":true}}"#,
            ),
            ("GET", "/auth/2sv/trust ", 204, "", ""),
            ("POST", "/setup/accountLogin", 200, "", account_body),
        ]);
        auth.state.session_token = Some("limited-token".into());
        auth.mark_sent(TwoFactorRoute::Sms(2, "sms".into()));
        assert!(matches!(
            auth.submit_2fa("123456").await,
            Err(AppError::TwoFactorRequired { .. })
        ));
        assert!(!auth.state.trusted_session);
        server.join().unwrap();
    }
}

#[test]
fn token_issuance_does_not_override_explicit_rejection_or_another_http_error() {
    assert!(!code_response_accepted(
        409,
        r#"{"securityCode":{"valid":false}}"#,
        true
    ));
    assert!(!code_response_accepted(
        400,
        r#"{"securityCode":{"valid":true}}"#,
        true
    ));
    assert!(!code_response_accepted(409, r#"{"hasError":true}"#, true));
}
