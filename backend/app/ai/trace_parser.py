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


    def parse_actions(self, trace_path: str, limit: int = 500) -> list:
        """Playwright trace zip → ordered list of API actions.

        Reads the `*.trace` NDJSON streams: each action is a `before` event
        (callId, apiName — or class/method on newer Playwright —, params,
        startTime) closed by an `after` event (endTime, error). Returns
        `[{call_id, api, selector, start_ms, duration_ms, error}]` in start
        order. Unknown/garbled lines are skipped; the trace format is
        Playwright-internal and drifts between versions, so this is
        best-effort by design. Not yet wired into failure analysis: traces
        exist only at capture level `full` and can be large, while per-step
        evidence already covers the same ground for every level.
        """
        if not os.path.exists(trace_path):
            return []
        calls: dict = {}
        order: list = []
        try:
            with zipfile.ZipFile(trace_path, 'r') as z:
                for name in z.namelist():
                    if not name.endswith('.trace'):
                        continue
                    with z.open(name) as f:
                        for raw in f:
                            try:
                                ev = json.loads(raw)
                            except (ValueError, UnicodeDecodeError):
                                continue
                            if not isinstance(ev, dict) or not ev.get('callId'):
                                continue
                            cid = ev['callId']
                            if ev.get('type') == 'before':
                                api = ev.get('apiName') or ev.get('title') or '.'.join(
                                    str(p) for p in (ev.get('class'), ev.get('method')) if p)
                                params = ev.get('params') if isinstance(ev.get('params'), dict) else {}
                                calls[cid] = {
                                    'call_id': cid, 'api': api or 'unknown',
                                    'selector': params.get('selector'),
                                    'start_ms': ev.get('startTime'), 'duration_ms': None, 'error': None,
                                }
                                order.append(cid)
                            elif ev.get('type') == 'after' and cid in calls:
                                start, end = calls[cid]['start_ms'], ev.get('endTime')
                                if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                                    calls[cid]['duration_ms'] = round(end - start, 1)
                                err = ev.get('error')
                                if isinstance(err, dict):
                                    err = err.get('message') or (err.get('error') or {}).get('message')
                                if err:
                                    calls[cid]['error'] = str(err)[:1000]
        except (zipfile.BadZipFile, OSError) as e:
            print(f"Error parsing trace actions: {e}")
            return []
        actions = [calls[c] for c in order]
        actions.sort(key=lambda a: a['start_ms'] if isinstance(a['start_ms'], (int, float)) else 0)
        return actions[:limit]


trace_parser = TraceParser()
