package tools

import (
	"testing"
)

func TestStringOr(t *testing.T) {
	if got := stringOr("hello", "default"); got != "hello" {
		t.Errorf("stringOr: expected hello, got %s", got)
	}
	if got := stringOr("", "default"); got != "default" {
		t.Errorf("stringOr: expected default, got %s", got)
	}
	if got := stringOr(nil, "default"); got != "default" {
		t.Errorf("stringOr: expected default, got %s", got)
	}
}

func TestStringOr_NonStringTypes(t *testing.T) {
	if got := stringOr(42, "default"); got != "default" {
		t.Errorf("stringOr(int): expected default, got %s", got)
	}
	if got := stringOr(true, "default"); got != "default" {
		t.Errorf("stringOr(bool): expected default, got %s", got)
	}
}
