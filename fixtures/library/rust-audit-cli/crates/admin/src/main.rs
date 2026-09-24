//! admin -- the audit service's HTTP surface.

mod routes;

#[tokio::main]
async fn main() {
    let app = routes::router();
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080")
        .await
        .expect("bind failed");
    axum::serve(listener, app).await.expect("serve failed");
}
