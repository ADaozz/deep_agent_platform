"""UI-only deterministic journal driver. Not mounted in production."""
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('PLATFORM_DATA_DIR',tempfile.mkdtemp(prefix='process-e2e-'))
from platform_app.main import app
from platform_app import domain as d,models as m,runtime as r
if not m.public()['profiles']:
    m.save({'default':'test/model','models':{'test':{'base_url':'https://example.com/v1','api_key':'test-only-no-network','api':'chat_completions','models':{'model':{}}}}},0)

@app.post('/__test/sessions/{session_id}/events')
def emit_events(session_id:str,events:list[dict]):
    runtime=r.runtime(session_id)
    with runtime.lock:
        for event in events:
            record=r.find(session_id)
            if event['type']=='operation' and event.get('operation')=='message':
                record['status']='running';record['messages'].append(dict(role='user',content=event.get('text',''),created_at=r.now()));record['error']=None
            if event['type']=='result':record['status']=event['status'];record['output']=event.get('output','')
            d.put('session',record)
            runtime.event(event)
    return dict(accepted=True)

if __name__=='__main__':
    import uvicorn
    print('PROCESS_E2E_DATA='+str(d.DATA),flush=True)
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('PROCESS_E2E_PORT','8096')),log_level='warning')
