import requests

OLLAMA_HOST = "http://localhost:11434"
MODEL_NAME = "llama3.2:1b"  # Change to your model name

def is_ollama_model_reachable(model_name):
    try:
        # Step 1: Check if Ollama server is running
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        response.raise_for_status()
        
        models = response.json().get("models", [])
        model_names = [m["name"] for m in models]
        
        # Step 2: Check if the model exists
        if model_name in model_names:
            print(f"✅ Model '{model_name}' is available.")
            return True
        else:
            print(f"❌ Model '{model_name}' not found. Available models: {model_names}")
            return False

    except requests.exceptions.ConnectionError:
        print("❌ Ollama server is not reachable. Is it running?")
        return False
    except requests.exceptions.Timeout:
        print("❌ Request timed out.")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

# Run the check
if __name__ == "__main__":
    is_ollama_model_reachable(MODEL_NAME)
