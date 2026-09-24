package com.example.orders;

import org.springframework.context.event.EventListener;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class OrderEvents {

    @KafkaListener(topics = "orders", groupId = "orders-api")
    public void onOrder(Order order) {
        System.out.println("received " + order.id());
    }

    @Scheduled(cron = "0 */5 * * * *")
    public void reconcile() {
        System.out.println("reconciling");
    }

    @EventListener
    public void onReady(Object event) {
        System.out.println("ready");
    }
}
