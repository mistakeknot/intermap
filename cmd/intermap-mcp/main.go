package main

import (
	"fmt"
	"os"

	"github.com/mark3labs/mcp-go/server"
	"github.com/mistakeknot/intermap/internal/client"
	"github.com/mistakeknot/intermap/internal/tools"
)

func main() {
	c := client.NewClient(
		client.WithBaseURL(os.Getenv("INTERMUTE_URL")),
	)

	s := server.NewMCPServer(
		"intermap",
		"0.1.0",
		server.WithToolCapabilities(true),
	)

	tools.RegisterAll(s, c)

	if err := server.ServeStdio(s); err != nil {
		fmt.Fprintf(os.Stderr, "intermap-mcp: %v\n", err)
		os.Exit(1)
	}
}
