import zipfile
import json
import os

class TraceParser:
    def parse_trace(self, trace_path: str):
        metrics = {"avg_latency": 0, "error_count": 0}
        if not os.path.exists(trace_path):
            return metrics
            
        try:
            with zipfile.ZipFile(trace_path, 'r') as z:
                # Find trace.network file
                # Playwright traces structure varies, but often trace.network is at root or in resources
                network_files = [f for f in z.namelist() if f.endswith("trace.network")]
                if not network_files:
                    return metrics
                
                # Use the largest network file if multiple (likely the main context)
                network_file = sorted(network_files, key=lambda x: z.getinfo(x).file_size, reverse=True)[0]
                
                with z.open(network_file) as f:
                    # trace.network is often JSONL
                    lines = f.readlines()
                    
                latencies = []
                errors = 0
                
                for line in lines:
                    try:
                        entry = json.loads(line)
                        # Simplified parsing logic
                        # We look for resource timing or response status
                        # Note: The actual format is internal to Playwright and may change.
                        # This is a best-effort implementation.
                        
                        # Example structure check (hypothetical based on common trace formats)
                        if "response" in entry:
                            resp = entry["response"]
                            if "status" in resp:
                                if resp["status"] >= 400:
                                    errors += 1
                            if "timing" in resp:
                                # timing: { startTime, domainLookupStart, ... }
                                # We need duration.
                                pass
                    except:
                        pass
                        
                metrics["error_count"] = errors
                # metrics["avg_latency"] = sum(latencies) / len(latencies) if latencies else 0
                
        except Exception as e:
            print(f"Error parsing trace: {e}")
            
        return metrics

trace_parser = TraceParser()
