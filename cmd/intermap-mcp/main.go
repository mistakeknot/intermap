package main

import (
	"fmt"
	"os"

	"github.com/mark3labs/mcp-go/server"
	"github.com/mistakeknot/interbase/mcputil"
	"github.com/mistakeknot/intermap/internal/client"
	"github.com/mistakeknot/intermap/internal/tools"
)

func main() {
	c := client.NewClient(
		client.WithBaseURL(os.Getenv("INTERMUTE_URL")),
	)

	metrics := mcputil.NewMetrics()
	s := server.NewMCPServer(
		"intermap",
		"0.1.0",
		server.WithToolCapabilities(true),
		server.WithToolHandlerMiddleware(metrics.Instrument()),
	)

	bridge := tools.RegisterAll(s, c)
	defer bridge.Close()

	if err := server.ServeStdio(s); err != nil {
		fmt.Fprintf(os.Stderr, "intermap-mcp: %v\n", err)
		os.Exit(1)
	}
}
