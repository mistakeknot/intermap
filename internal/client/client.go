package client

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"time"
)

// Agent represents an agent registered with intermute.
type Agent struct {
	AgentID   string `json:"agent_id"`
	Name      string `json:"name"`
	Project   string `json:"project"`
	Status    string `json:"status"`
	SessionID string `json:"session_id,omitempty"`
	LastSeen  string `json:"last_seen,omitempty"`
}

// Reservation represents a file reservation.
type Reservation struct {
	ID        string `json:"id"`
	AgentID   string `json:"agent_id"`
	Pattern   string `json:"pattern"`
	Reason    string `json:"reason"`
	Project   string `json:"project"`
	IsActive  bool   `json:"is_active"`
	CreatedAt string `json:"created_at,omitempty"`
}

// Client wraps the intermute HTTP API.
type Client struct {
	baseURL string
	http    *http.Client
}

// Option configures the client.
type Option func(*Client)

// NewClient creates a new intermute client.
func NewClient(opts ...Option) *Client {
	c := &Client{
		http: &http.Client{Timeout: 5 * time.Second},
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// WithBaseURL sets the base URL for the intermute API.
func WithBaseURL(url string) Option {
	return func(c *Client) {
		c.baseURL = url
	}
}

// Available returns true if the client has a configured URL.
func (c *Client) Available() bool {
	return c.baseURL != ""
}

// ListAgents returns all active agents.
func (c *Client) ListAgents(ctx context.Context) ([]Agent, error) {
	if !c.Available() {
		return nil, nil
	}

	req, err := http.NewRequestWithContext(ctx, "GET", c.baseURL+"/api/agents", nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("list agents: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("list agents: HTTP %d", resp.StatusCode)
	}

	var agents []Agent
	if err := json.NewDecoder(resp.Body).Decode(&agents); err != nil {
		return nil, fmt.Errorf("decode agents: %w", err)
	}
	return agents, nil
}

// ListReservations returns all reservations, optionally filtered by project.
func (c *Client) ListReservations(ctx context.Context, project string) ([]Reservation, error) {
	if !c.Available() {
		return nil, nil
	}

	reqURL := c.baseURL + "/api/reservations"
	if project != "" {
		reqURL += "?project=" + url.QueryEscape(project)
	}

	req, err := http.NewRequestWithContext(ctx, "GET", reqURL, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("list reservations: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("list reservations: HTTP %d", resp.StatusCode)
	}

	var reservations []Reservation
	if err := json.NewDecoder(resp.Body).Decode(&reservations); err != nil {
		return nil, fmt.Errorf("decode reservations: %w", err)
	}
	return reservations, nil
}
