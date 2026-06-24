"""Pytest configuration — shared fixtures and test setup."""
import os
import sys

# Ensure sgos-backend is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use the real database for integration tests (read-only queries)
# For write tests, we use the test DB path but init tables first
TEST_DB = os.path.join(os.path.dirname(__file__), "test_sgos.db")
