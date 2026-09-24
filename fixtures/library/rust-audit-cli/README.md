# audit

Audit a Redis cache and write a report.

The workspace has two crates: `audit`, the command line tool, and `admin`, the HTTP
surface that exposes the same data to an operator.

## Commands

```
audit fetch <url>   # pull the feed and cache every entry
audit report        # build a report from the cached entries
audit status        # show how many entries are cached
```

`audit-report` is a second binary that prints the cached report as JSON.
