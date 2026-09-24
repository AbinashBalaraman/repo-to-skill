package commands

import (
	"github.com/spf13/cobra"
)

var fetchCmd = &cobra.Command{
	Use:   "fetch",
	Short: "Pull new messages from the queue",
	RunE: func(cmd *cobra.Command, args []string) error {
		return drain(args)
	},
}

var reportCmd = &cobra.Command{
	Use:   "report",
	Short: "Print a summary of drained messages",
	RunE: func(cmd *cobra.Command, args []string) error {
		return summarise(args)
	},
}

func drain(args []string) error {
	return nil
}

func summarise(args []string) error {
	return nil
}
