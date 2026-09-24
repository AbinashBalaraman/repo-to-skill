# orders-api

Order intake service. Accepts orders over HTTP, streams them onto Kafka and reconciles
them against the warehouse on a schedule.

## Endpoints

```
GET    /api/orders
GET    /api/orders/{id}
POST   /api/orders
DELETE /api/orders/{id}
```

## Configuration

`KAFKA_BOOTSTRAP_SERVERS` and `SPRING_DATASOURCE_URL` are read from the environment.
