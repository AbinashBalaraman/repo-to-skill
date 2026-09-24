// Command queue-runner drains a queue and republishes to a webhook.
package main

import "github.com/example/queue-runner/internal/commands"

func main() {
	commands.Execute()
}
