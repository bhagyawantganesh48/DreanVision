import os
import logging
from pymongo import MongoClient
from dotenv import load_dotenv

logger = logging.getLogger("dreamvision.mongodb")

def get_mongo_client() -> MongoClient:
    """
    Initializes and returns the MongoDB Atlas client connection 
    based on the .env file configuration.
    """
    load_dotenv()
    
    # We load MONGODB_URI. If not present, we will fallback to a local string or None 
    # to avoid violently crashing the local Edge application if internet is out.
    mongo_uri = os.environ.get("MONGODB_URI")
    
    if not mongo_uri:
        logger.warning("MONGODB_URI not found in environment. Cloud sync disabled.")
        return None
        
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        # Attempt to see if we can actively ping the server
        client.admin.command('ping')
        return client
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB Atlas cluster: {e}")
        return None

def get_inspections_collection(client: MongoClient):
    """
    Returns the 'inspections' MongoDB collection.
    """
    if client is None:
        return None
    db = client["dreamvision"]
    return db["inspections"]

if __name__ == "__main__":
    # Test script if executed directly
    logging.basicConfig(level=logging.INFO)
    print("Testing MongoDB Client Initialisation...")
    cli = get_mongo_client()
    if cli:
        print("Successfully connected to the Atlas Cluster!")
        cli.close()
    else:
        print("Connection failed or skipped.")
