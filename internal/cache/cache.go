package cache

import (
	"sync"
	"time"
)

// Cache is a generic mtime-based cache with LRU eviction.
type Cache[T any] struct {
	mu      sync.Mutex
	entries map[string]*entry[T]
	ttl     time.Duration
	maxSize int
}

type entry[T any] struct {
	value     T
	cachedAt  time.Time
	mtimeHash string
	lastUsed  time.Time
}

// New creates a cache with the given TTL and max entries.
func New[T any](ttl time.Duration, maxSize int) *Cache[T] {
	if maxSize <= 0 {
		maxSize = 10
	}
	return &Cache[T]{
		entries: make(map[string]*entry[T]),
		ttl:     ttl,
		maxSize: maxSize,
	}
}

// Get returns the cached value if the key matches, mtime matches, and TTL hasn't expired.
func (c *Cache[T]) Get(key string, mtimeHash string) (T, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()

	e, ok := c.entries[key]
	if !ok {
		var zero T
		return zero, false
	}
	if e.mtimeHash != mtimeHash || time.Since(e.cachedAt) > c.ttl {
		delete(c.entries, key)
		var zero T
		return zero, false
	}
	e.lastUsed = time.Now()
	return e.value, true
}

// Put stores a value, evicting the LRU entry if at capacity.
func (c *Cache[T]) Put(key string, mtimeHash string, value T) {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()

	// Evict LRU if at capacity and this is a new key
	if _, exists := c.entries[key]; !exists && len(c.entries) >= c.maxSize {
		c.evictLRU()
	}

	c.entries[key] = &entry[T]{
		value:     value,
		cachedAt:  now,
		mtimeHash: mtimeHash,
		lastUsed:  now,
	}
}

// Invalidate removes a cache entry.
func (c *Cache[T]) Invalidate(key string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.entries, key)
}

func (c *Cache[T]) evictLRU() {
	var oldestKey string
	var oldestTime time.Time
	first := true

	for key, e := range c.entries {
		if first || e.lastUsed.Before(oldestTime) {
			oldestKey = key
			oldestTime = e.lastUsed
			first = false
		}
	}
	if oldestKey != "" {
		delete(c.entries, oldestKey)
	}
}
