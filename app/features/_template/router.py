"""Feature template — copy this directory to start a new feature.

Steps:
1. Copy _template/ to a new directory under features/
2. Rename this file's router prefix and tags
3. Add routes that call services via Depends()
4. Wire the router in app/main.py:
   from app.features.your_feature.router import router as your_router
   app.include_router(your_router)
"""

from fastapi import APIRouter

router = APIRouter(prefix="/your-prefix", tags=["your-feature"])

# Add routes here. Keep them thin:
# - Parse the request
# - Call a service method
# - Return a response
