use serde::Serialize;
use thiserror::Error;

pub type Result<T> = std::result::Result<T, AppError>;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("{message}")]
    AuthRequired { message: String, detail: String },
    #[error("{message}")]
    TwoFactorRequired { message: String, detail: String },
    #[error("{message}")]
    TermsRequired { message: String, detail: String },
    #[error("{message}")]
    Network { message: String, detail: String },
    #[error("{message}")]
    Conflict { message: String, detail: String },
    #[error("{message}")]
    BadRequest { message: String, detail: String },
    #[error("{message}")]
    Internal { message: String, detail: String },
}

#[derive(Debug, Serialize)]
pub struct ErrorBody<'a> {
    pub code: &'a str,
    pub message: &'a str,
    pub detail: &'a str,
}

impl AppError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::AuthRequired { .. } => "AUTH_REQUIRED",
            Self::TwoFactorRequired { .. } => "2FA_REQUIRED",
            Self::TermsRequired { .. } => "TERMS_REQUIRED",
            Self::Network { .. } => "NETWORK",
            Self::Conflict { .. } => "CONFLICT",
            Self::BadRequest { .. } => "BAD_REQUEST",
            Self::Internal { .. } => "ERROR",
        }
    }

    pub fn body(&self) -> ErrorBody<'_> {
        let (message, detail) = match self {
            Self::AuthRequired { message, detail }
            | Self::TwoFactorRequired { message, detail }
            | Self::TermsRequired { message, detail }
            | Self::Network { message, detail }
            | Self::Conflict { message, detail }
            | Self::BadRequest { message, detail }
            | Self::Internal { message, detail } => (message.as_str(), detail.as_str()),
        };
        ErrorBody {
            code: self.code(),
            message,
            detail,
        }
    }

    pub fn internal(message: impl Into<String>, detail: impl Into<String>) -> Self {
        Self::Internal {
            message: message.into(),
            detail: detail.into(),
        }
    }

    pub fn bad_request(message: impl Into<String>) -> Self {
        Self::BadRequest {
            message: message.into(),
            detail: String::new(),
        }
    }
}

impl From<rusqlite::Error> for AppError {
    fn from(value: rusqlite::Error) -> Self {
        Self::internal("The local reminder cache failed", value.to_string())
    }
}

impl From<serde_json::Error> for AppError {
    fn from(value: serde_json::Error) -> Self {
        Self::internal("Invalid JSON data", value.to_string())
    }
}

impl From<reqwest::Error> for AppError {
    fn from(value: reqwest::Error) -> Self {
        let detail = if value.is_timeout() {
            "request timed out"
        } else if value.is_connect() {
            "connection failed"
        } else if value.is_decode() {
            "response decoding failed"
        } else {
            "request failed"
        };
        Self::Network {
            message: "Could not reach iCloud".into(),
            detail: detail.into(),
        }
    }
}
