# queue-runner

Drain a queue and republish each message to a webhook.

The command line is built with cobra; the HTTP surface is a gin router plus a couple of
`net/http` handlers.

## Commands

```
queue-runner fetch    # pull new messages from the queue
queue-runner report   # print a summary of drained messages
```

## Endpoints

```
GET    /healthz
GET    /readyz
GET    /messages
POST   /messages
DELETE /messages/:id
```
