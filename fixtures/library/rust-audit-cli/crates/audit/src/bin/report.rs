//! A second binary: print the cached report as JSON.

use clap::Parser;

#[derive(Parser)]
#[command(name = "audit-report", about = "Print the cached report as JSON")]
struct ReportArgs {
    /// How many entries to include.
    #[arg(long, default_value_t = 50)]
    limit: usize,
}

fn main() {
    let args = ReportArgs::parse();
    println!("{{\"limit\": {}}}", args.limit);
}
