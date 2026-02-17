package cache

import (
	"testing"
	"time"
)

func TestCache_GetPut(t *testing.T) {
	c := New[string](5*time.Minute, 10)

	// Miss on empty cache
	_, ok := c.Get("key1", "hash1")
	if ok {
		t.Error("expected miss on empty cache")
	}

	// Put and get
	c.Put("key1", "hash1", "value1")
	v, ok := c.Get("key1", "hash1")
	if !ok {
		t.Error("expected hit after put")
	}
	if v != "value1" {
		t.Errorf("expected value1, got %s", v)
	}
}

func TestCache_MtimeInvalidation(t *testing.T) {
	c := New[string](5*time.Minute, 10)
	c.Put("key1", "hash1", "value1")

	// Different mtime hash should miss
	_, ok := c.Get("key1", "hash2")
	if ok {
		t.Error("expected miss for different mtime hash")
	}
}

func TestCache_TTLExpiry(t *testing.T) {
	c := New[string](50*time.Millisecond, 10)
	c.Put("key1", "hash1", "value1")

	// Should hit immediately
	_, ok := c.Get("key1", "hash1")
	if !ok {
		t.Error("expected hit before TTL")
	}

	// Wait for TTL
	time.Sleep(60 * time.Millisecond)
	_, ok = c.Get("key1", "hash1")
	if ok {
		t.Error("expected miss after TTL")
	}
}

func TestCache_LRUEviction(t *testing.T) {
	c := New[string](5*time.Minute, 2)

	c.Put("key1", "h1", "v1")
	c.Put("key2", "h2", "v2")

	// Access key1 to make it recently used
	c.Get("key1", "h1")

	// Adding key3 should evict key2 (least recently used)
	c.Put("key3", "h3", "v3")

	_, ok := c.Get("key1", "h1")
	if !ok {
		t.Error("key1 should still be cached (recently accessed)")
	}

	_, ok = c.Get("key2", "h2")
	if ok {
		t.Error("key2 should have been evicted (LRU)")
	}

	_, ok = c.Get("key3", "h3")
	if !ok {
		t.Error("key3 should be cached (just added)")
	}
}

func TestCache_Invalidate(t *testing.T) {
	c := New[string](5*time.Minute, 10)
	c.Put("key1", "hash1", "value1")

	c.Invalidate("key1")

	_, ok := c.Get("key1", "hash1")
	if ok {
		t.Error("expected miss after invalidation")
	}
}
