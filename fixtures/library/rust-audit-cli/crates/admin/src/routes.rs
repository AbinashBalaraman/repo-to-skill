//! Route declarations.

use axum::routing::{get, post};
use axum::Router;

pub fn router() -> Router {
    let metrics_endpoint = Router::new().route("/metrics", get(metrics));
    Router::new()
        .route("/health", get(health))
        .route("/audit", post(start_audit))
        .route("/audit/status", get(audit_status))
        .service(metrics_endpoint)
}

async fn health() -> &'static str {
    "ok"
}

async fn start_audit() -> &'static str {
    "started"
}

async fn audit_status() -> &'static str {
    "idle"
}

async fn metrics() -> &'static str {
    "# audit metrics\n"
}
