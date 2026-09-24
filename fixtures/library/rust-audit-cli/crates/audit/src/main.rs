//! audit -- audit a cache and publish a report.

mod cache;
mod cli;

use clap::Parser;
use cli::{Cli, Commands};

#[tokio::main]
async fn main() {
    let args = Cli::parse();
    let outcome = match args.command {
        Commands::Fetch { url } => cache::fetch(&url).await,
        Commands::Report { limit } => cache::report(limit).await,
        Commands::Status => cache::status().await,
    };
    match outcome {
        Ok(text) => println!("{text}"),
        Err(err) => {
            eprintln!("audit failed: {err}");
            std::process::exit(1);
        }
    }
}
