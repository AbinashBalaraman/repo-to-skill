//! The command line surface.

use clap::{Parser, Subcommand};

/// Audit a cache and publish a report.
#[derive(Parser)]
#[command(name = "audit", about = "Audit a cache and publish a report")]
pub struct Cli {
    /// The subcommand to run.
    #[command(subcommand)]
    pub command: Commands,
}

/// Every audit subcommand.
#[derive(Subcommand)]
pub enum Commands {
    /// Pull the feed and cache each entry.
    Fetch {
        /// Feed URL to pull.
        url: String,
    },
    /// Build a report from the cached entries.
    #[command(name = "report")]
    Report {
        /// How many entries to include.
        limit: usize,
    },
    /// Show how many entries are cached.
    Status,
}
