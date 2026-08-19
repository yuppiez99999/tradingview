"""Utilities for working with Ollama models in Docker environments"""

import time

import questionary
import requests


def ensure_ollama_and_model(model_name: str, ollama_url: str) -> bool:
    """Ensure the Ollama model is available at the target Ollama endpoint."""

    # Step 1: Check if Ollama service is available
    if not is_ollama_available(ollama_url):
        return False

    # Step 2: Check if model is already available
    available_models = get_available_models(ollama_url)
    if model_name in available_models:
        return True

    # Step 3: Model not available - ask if user wants to download

    if not questionary.confirm(f"Do you want to download {model_name}?").ask():
        return False

    # Step 4: Download the model
    return download_model(model_name, ollama_url)


def is_ollama_available(ollama_url: str) -> bool:
    """Check if Ollama service is available in Docker environment."""
    try:
        response = requests.get(f"{ollama_url}/api/version", timeout=5)
        if response.status_code == 200:
            return True

        return False
    except requests.RequestException:
        return False


def get_available_models(ollama_url: str) -> list:
    """Get list of available models in Docker environment."""
    try:
        response = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            return [m["name"] for m in models]

        return []
    except requests.RequestException:
        return []


def download_model(model_name: str, ollama_url: str) -> bool:
    """Download a model in Docker environment."""

    # Step 1: Initiate the download
    try:
        response = requests.post(f"{ollama_url}/api/pull", json={"name": model_name}, timeout=10)
        if response.status_code != 200:
            if response.text:
                pass
            return False
    except requests.RequestException:
        return False

    # Step 2: Monitor the download progress

    total_wait_time = 0
    max_wait_time = 1800  # 30 minutes max wait
    check_interval = 10  # Check every 10 seconds

    while total_wait_time < max_wait_time:
        # Check if the model has been downloaded
        available_models = get_available_models(ollama_url)
        if model_name in available_models:
            return True

        # Wait before checking again
        time.sleep(check_interval)
        total_wait_time += check_interval

        # Print a status message every minute
        if total_wait_time % 60 == 0:
            total_wait_time // 60

    # If we get here, we've timed out
    return False


def delete_model(model_name: str, ollama_url: str) -> bool:
    """Delete a model in Docker environment."""

    try:
        response = requests.delete(f"{ollama_url}/api/delete", json={"name": model_name}, timeout=10)
        if response.status_code == 200:
            return True
        else:
            if response.text:
                pass
            return False
    except requests.RequestException:
        return False
