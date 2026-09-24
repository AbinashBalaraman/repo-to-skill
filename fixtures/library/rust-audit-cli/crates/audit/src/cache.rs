//! Cache access.

use redis::AsyncCommands;

const REDIS_URL: &str = "redis://127.0.0.1/";

pub async fn fetch(url: &str) -> Result<String, redis::RedisError> {
    let client = redis::Client::open(REDIS_URL)?;
    let mut connection = client.get_async_connection().await?;
    let _: () = connection.set(url, "cached").await?;
    Ok(format!("cached {url}"))
}

pub async fn report(limit: usize) -> Result<String, redis::RedisError> {
    let mut connection = redis::Client::open(REDIS_URL)?
        .get_async_connection()
        .await?;
    let entries: Vec<String> = connection.keys("*").await?;
    Ok(format!("report of up to {limit} entries"))
}

pub async fn status() -> Result<String, redis::RedisError> {
    let mut connection = redis::Client::open(REDIS_URL)?
        .get_async_connection()
        .await?;
    let count: usize = connection.dbsize().await?;
    Ok(format!("{count} entries cached"))
}
