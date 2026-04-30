import uvicorn
from fastapi import FastAPI
from titiler.core.factory import TilerFactory

# Ek naya, saaf FastAPI app
app = FastAPI(title="TiTiler Test Server")

# TiTiler ka default setup
cog = TilerFactory()
app.include_router(cog.router)

if __name__ == "__main__":
    print("Test Server chal raha hai http://127.0.0.1:8080 par...")
    uvicorn.run(app, host="127.0.0.1", port=8080)