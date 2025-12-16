import os
import asyncio
from playwright.async_api import BrowserContext
from app.runner.browser_manager import BrowserManager
from app.core.storage import minio_client

class AsyncPlaywrightRunner:
    def __init__(self):
        self.browser_manager = BrowserManager()

    async def setup_hybrid_context(self, context: BrowserContext):
        """
        Injects API authentication state into the browser context.
        """
        # Example:
        # api = context.request
        # await api.post("https://example.com/api/login", ...)
        pass

    async def run_test(self, run_id: int):
        await self.browser_manager.start()
        
        # Create artifacts directory
        artifacts_dir = f"/tmp/artifacts/{run_id}"
        os.makedirs(artifacts_dir, exist_ok=True)
        
        try:
            context = await self.browser_manager.create_context(video_dir=artifacts_dir)
            
            # Hybrid Context Setup
            await self.setup_hybrid_context(context)
            
            await self.browser_manager.start_tracing()
            
            page = await context.new_page()
            
            # TODO: In a real app, we would inject the test code here.
            # For this MVP, we'll hardcode a simple test that visits example.com
            
            print(f"Running test for run_id: {run_id}")
            start_time = asyncio.get_event_loop().time()
            
            await page.goto("https://example.com")
            
            # Simulate some interaction
            # await page.click("text=More information...")
            
            end_time = asyncio.get_event_loop().time()
            duration_ms = (end_time - start_time) * 1000
            
            # Stop tracing
            trace_path = os.path.join(artifacts_dir, "trace.zip")
            await self.browser_manager.stop_tracing(path=trace_path)
            
            # Upload artifacts
            trace_key = f"runs/{run_id}/trace.zip"
            minio_client.upload_file(trace_path, trace_key)
            
            # Find video file
            video_key = None
            # Close context to ensure video is saved
            await self.browser_manager.context.close()
            
            # Now upload video
            video_files = [f for f in os.listdir(artifacts_dir) if f.endswith(".webm")]
            if video_files:
                video_path = os.path.join(artifacts_dir, video_files[0])
                video_key = f"runs/{run_id}/video.webm"
                minio_client.upload_file(video_path, video_key)

            return {
                "status": "passed",
                "trace": trace_key,
                "video": video_key,
                "duration_ms": duration_ms
            }
            
        except Exception as e:
            # Capture trace on failure too
            trace_path = os.path.join(artifacts_dir, "trace.zip")
            await self.browser_manager.stop_tracing(path=trace_path)
            trace_key = f"runs/{run_id}/trace.zip"
            minio_client.upload_file(trace_path, trace_key)
            return {
                "status": "failed",
                "error": str(e),
                "trace": trace_key,
                "duration_ms": 0
            }
            
        finally:
            await self.browser_manager.close()
