// Package commands wires the queue-runner command line together.
package commands

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

// RootCmd is the queue-runner root command.
var RootCmd = &cobra.Command{
	Use:   "queue-runner",
	Short: "Drain a queue and republish to a webhook",
}

// Execute runs the root command and exits non-zero on failure.
func Execute() {
	if err := RootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func init() {
	RootCmd.AddCommand(fetchCmd)
	RootCmd.AddCommand(reportCmd)
}
