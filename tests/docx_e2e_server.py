"""Deterministic formatting/steering fixture; production AgentRunner is unchanged."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('PLATFORM_DATA_DIR',tempfile.mkdtemp(prefix='docx-e2e-'))
from docx_corpus import formatted_operations, make_assets
from platform_app import domain as d, models as m
from platform_app.main import app
from platform_app.documents import DocumentStore
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatResult, ChatGeneration
import agent.llm
import agent.runner

m.save({'default':'test/model','models':{'test':{'base_url':'https://example.com/v1','api_key':'scripted-local-test','api':'chat_completions','models':{'model':{}}}}},0)


class FormattingModel(BaseChatModel):
    @property
    def _llm_type(self): return 'formatted-docx-e2e'
    def bind_tools(self,tools,**kwargs): return self
    def _generate(self,messages,**kwargs):
        time.sleep(.08)
        root=d.files_root(); root.mkdir(parents=True,exist_ok=True)
        path=root/'report.docx'
        if not path.exists():
            make_assets(root)
            name,args='docx_init',dict(document_id='report')
        else:
            store=DocumentStore(path); state=store.status(); revision=state['revision']
            ids={b['block_id'] for b in state['blocks']}
            history={r['operation_id'] for r in state['history']}
            correction=any(isinstance(msg,HumanMessage) and '第二节改成表格' in str(msg.content) for msg in messages)
            operation=None
            if state['gate']=='failed':
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='排版检查失败，保留草稿等待修复。'))])
            if state['gate']!='open':
                name,args='docx_checkpoint',dict(document_id='report')
            elif correction and 'correct-remove' not in history and 'p-2' in ids:
                operation=dict(type='delete_block',block_id='p-2'); operation_id='correct-remove'
            elif correction and 'correct-remove' in history and 'correct-table' not in history:
                operation=dict(type='append_table',block_id='business-correction',after_block_id='sh-2',column_widths_twips=[2100,4200,2700],
                    rows=[['协同事项','业务表达','责任部门'],['职责分工','确认办理范围与服务承诺','业务部门'],['过程协同','跟踪进度并处理交接问题','交付团队']]); operation_id='correct-table'
            else:
                plan=formatted_operations(sections=12)
                next_op=next(((i,op) for i,op in enumerate(plan) if f'plan-{i:03d}' not in history),None)
                if next_op:
                    i,operation=next_op; operation_id=f'plan-{i:03d}'
                elif not state['pages'] or any(not p['complete'] for p in state['pages']):
                    name,args='docx_checkpoint',dict(document_id='report',final=True)
                else:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content='带格式的报告草稿已通过自动排版检查；所有页面仍待实际视觉审查。'))])
            if operation:
                name,args='docx_apply_one',dict(document_id='report',operation_id=operation_id,base_revision=revision,operation=operation)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[dict(name=name,args=args,id=str(time.time_ns()),type='tool_call')]))])

agent.llm.build_chat_model=lambda *a,**k:FormattingModel()
agent.runner.build_chat_model=lambda *a,**k:FormattingModel()
if __name__=='__main__':
    import uvicorn
    print('DOCX_E2E_DATA='+str(d.DATA),flush=True)
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('DOCX_E2E_PORT','8081')),log_level='warning')
