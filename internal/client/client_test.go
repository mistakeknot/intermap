package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestListAgents(t *testing.T) {
	agents := []Agent{
		{AgentID: "a1", Name: "builder", Project: "interlock", Status: "active"},
		{AgentID: "a2", Name: "reviewer", Project: "clavain", Status: "active"},
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/agents" {
			http.NotFound(w, r)
			return
		}
		json.NewEncoder(w).Encode(agents)
	}))
	defer ts.Close()

	c := NewClient(WithBaseURL(ts.URL))
	got, err := c.ListAgents(context.Background())
	if err != nil {
		t.Fatalf("ListAgents: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 agents, got %d", len(got))
	}
	if got[0].Name != "builder" {
		t.Errorf("expected builder, got %s", got[0].Name)
	}
}

func TestListReservations(t *testing.T) {
	reservations := []Reservation{
		{ID: "r1", AgentID: "a1", Pattern: "internal/tools/*.go", Project: "interlock", IsActive: true},
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/reservations" {
			http.NotFound(w, r)
			return
		}
		json.NewEncoder(w).Encode(reservations)
	}))
	defer ts.Close()

	c := NewClient(WithBaseURL(ts.URL))
	got, err := c.ListReservations(context.Background(), "")
	if err != nil {
		t.Fatalf("ListReservations: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 reservation, got %d", len(got))
	}
	if got[0].Pattern != "internal/tools/*.go" {
		t.Errorf("expected internal/tools/*.go, got %s", got[0].Pattern)
	}
}

func TestClient_Unavailable(t *testing.T) {
	c := NewClient() // no base URL

	agents, err := c.ListAgents(context.Background())
	if err != nil {
		t.Fatalf("expected nil error for unavailable client, got %v", err)
	}
	if agents != nil {
		t.Errorf("expected nil agents for unavailable client")
	}
}

func TestClient_ServerDown(t *testing.T) {
	c := NewClient(WithBaseURL("http://127.0.0.1:1")) // port that's definitely not listening

	_, err := c.ListAgents(context.Background())
	if err == nil {
		t.Error("expected error for unreachable server")
	}
}

func TestListReservations_WithProject(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/reservations" {
			http.NotFound(w, r)
			return
		}
		project := r.URL.Query().Get("project")
		if project != "interlock" {
			json.NewEncoder(w).Encode([]Reservation{})
			return
		}
		json.NewEncoder(w).Encode([]Reservation{
			{ID: "r1", AgentID: "a1", Pattern: "*.go", Project: "interlock", IsActive: true},
		})
	}))
	defer ts.Close()

	c := NewClient(WithBaseURL(ts.URL))
	got, err := c.ListReservations(context.Background(), "interlock")
	if err != nil {
		t.Fatalf("ListReservations: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 reservation, got %d", len(got))
	}
}

func TestListAgents_HTTPError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer ts.Close()

	c := NewClient(WithBaseURL(ts.URL))
	_, err := c.ListAgents(context.Background())
	if err == nil {
		t.Error("expected error for HTTP 500")
	}
}
