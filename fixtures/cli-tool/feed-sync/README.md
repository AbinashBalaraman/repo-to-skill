# feed-sync

Fetch an RSS feed, store new entries, and publish a digest to a webhook.

## Commands

```
feed-sync fetch     # pull the feed and store new entries
feed-sync digest    # build a digest from stored entries
feed-sync publish   # send the digest to the configured webhook
feed-sync status    # show how many entries are stored
```

## Configuration

Set `FEED_URL` and `WEBHOOK_URL` in the environment. Both are required for
`fetch` and `publish` respectively.

## Warnings

Publishing sends the digest to an external webhook and **cannot be undone** —
the receiving system keeps a permanent copy. There is no retract.

Delivery to the webhook **costs money** on the metered plan after the first
1,000 calls per month.

The stored entries may include author names and email addresses from the feed,
which is **personal data** under GDPR. The `status` command never prints them.

Feed content is the copyright of its authors; `digest` includes attribution and
`publish` refuses to run if attribution is missing.
