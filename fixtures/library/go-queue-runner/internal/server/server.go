// Package server exposes the runner's HTTP surface.
package server

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// Serve registers every route and blocks until the server stops.
func Serve() error {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", health)
	http.HandleFunc("/readyz", ready)

	router := gin.Default()
	router.GET("/messages", listMessages)
	router.POST("/messages", enqueueMessage)
	router.DELETE("/messages/:id", dropMessage)

	return http.ListenAndServe(":8080", mux)
}

func health(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}

func ready(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}

func listMessages(c *gin.Context) {}

func enqueueMessage(c *gin.Context) {}

func dropMessage(c *gin.Context) {}
