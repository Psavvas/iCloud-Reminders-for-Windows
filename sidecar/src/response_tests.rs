use super::{bounded_response_text, bounded_response_text_with_limit};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::time::Duration;

async fn response(wire: Vec<u8>) -> reqwest::Response {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut request = Vec::new();
        let mut byte = [0];
        while !request.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).unwrap();
            request.push(byte[0]);
            assert!(request.len() < 8192);
        }
        // An oversized response is deliberately closed early by the client.
        let _ = stream.write_all(&wire);
    });
    reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap()
        .get(format!("http://{address}/"))
        .send()
        .await
        .unwrap()
}

#[tokio::test]
async fn cloudkit_accepts_record_data_larger_than_the_authentication_budget() {
    let body = format!(
        r#"{{"records":[{{"notes":"{}"}}]}}"#,
        "x".repeat(2 * 1024 * 1024)
    );
    let wire = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let text = bounded_response_text_with_limit(
        response(wire.into_bytes()).await,
        crate::cloudkit::MAX_CLOUDKIT_RESPONSE_BYTES,
    )
    .await
    .unwrap();
    assert_eq!(text, body);
    assert!(serde_json::from_str::<serde_json::Value>(&text).is_ok());
}

#[tokio::test]
async fn declared_oversize_is_rejected_for_both_budgets() {
    let wire = b"HTTP/1.1 200 OK\r\nContent-Length: 2097153\r\nConnection: close\r\n\r\n";
    assert!(
        bounded_response_text(response(wire.to_vec()).await)
            .await
            .is_err()
    );
    let wire = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        crate::cloudkit::MAX_CLOUDKIT_RESPONSE_BYTES + 1
    );
    assert!(
        bounded_response_text_with_limit(
            response(wire.into_bytes()).await,
            crate::cloudkit::MAX_CLOUDKIT_RESPONSE_BYTES
        )
        .await
        .is_err()
    );
}

#[tokio::test]
async fn chunked_responses_enforce_the_total_decoded_limit() {
    let wire = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n3\r\nabc\r\n3\r\ndef\r\n0\r\n\r\n";
    assert_eq!(
        bounded_response_text_with_limit(response(wire.to_vec()).await, 6)
            .await
            .unwrap(),
        "abcdef"
    );
    let error = bounded_response_text_with_limit(response(wire.to_vec()).await, 5)
        .await
        .unwrap_err();
    assert!(error.body().detail.contains("5-byte limit"));
}
