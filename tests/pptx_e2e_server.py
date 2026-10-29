"""Real AgentRunner, scoped approval, Impress and SSE; deterministic test model."""
import os
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('PLATFORM_DATA_DIR',tempfile.mkdtemp(prefix='pptx-e2e-'))
from pptx_corpus import make_template,plan,cards
from platform_app import domain as d,models as m,format_plugins
from platform_app.main import app
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage,HumanMessage
from langchain_core.outputs import ChatResult,ChatGeneration
import agent.llm,agent.runner
if not m.public()['profiles']:
 m.save({'default':'test/model','models':{'test':{'base_url':'https://example.com/v1','api_key':'scripted-local-test','api':'chat_completions','models':{'model':{}}}}},0)
engine=format_plugins.load_plugin('pptx').engine
class Model(BaseChatModel):
    @property
    def _llm_type(self):return 'pptx-scoped-formatted-e2e'
    def bind_tools(self,tools,**kwargs):return self
    def _generate(self,messages,**kwargs):
        time.sleep(.13)
        root=d.DATA/'workspaces'/'office'/'files';root.mkdir(parents=True,exist_ok=True)
        path=root/'project-report.pptx'
        if not path.exists():
            template=make_template(root);name,args='pptx_init',dict(presentation_id='project-report',template=template.name)
        else:
            state=engine.PresentationStore(path).status();rev=state['revision'];history={h['operation_id'] for h in state['history']};slides=state['manifest']['slides'];first=slides[0]['slide_id']
            text='\n'.join(str(msg.content) for msg in messages if isinstance(msg,HumanMessage))
            operation=None;opid=None
            if '蓝白' in text and 'feedback-theme' not in history:operation=dict(operation='presentation.set_theme',theme='corporate_blue');opid='feedback-theme'
            elif '替换为架构图' in text and any(s['slide_id']=='slide-05' and (any(e['id']=='process-list' for e in s['elements']) or 'feedback-layout-0' in history) for s in slides):
                corrections=[dict(operation='element.remove',slide_id='slide-05',element=dict(id='process-list')),dict(operation='slide.set_layout',slide_id='slide-05',layout='three_columns'),*cards('slide-05',['目标输入','受控执行','质量交付'],['用户目标\n模板与约束\n实时纠偏','主 Agent\n原子工具\n审批边界','页面检查\n版本恢复\n可编辑文件'],prefix='flow')]
                todo=next(((i,op) for i,op in enumerate(corrections) if f'feedback-layout-{i}' not in history),None)
                if todo:i,operation=todo;opid=f'feedback-layout-{i}'
            if operation is None and '修改第3页' in text and 'feedback-history' not in history and any(s['slide_id']=='slide-09' for s in slides):operation=dict(operation='element.update',slide_id='slide-03',element=dict(id='architecture-body-1',text='主 Agent 统一驱动\n安全检查点纠偏\n持久化执行记录'));opid='feedback-history'
            if operation is None:
                todo=next(((i,op) for i,op in enumerate(plan(first)) if f'plan-{i:03d}' not in history),None)
                if todo:
                    i,operation=todo;opid=f'plan-{i:03d}'
                    if operation['operation']=='slide.add':
                        pending=next((s for s in slides if not s['review'].get('automatic_passed')),None)
                        if pending:
                            if pending['review'].get('status')=='blocked':return ChatResult(generations=[ChatGeneration(message=AIMessage(content='页面检查失败，需要修复，草稿已保留。'))])
                            operation=None;name,args='pptx_check_slide',dict(presentation_id='project-report',slide_id=pending['slide_id'],content_notes='核对本页标题、短正文、原生对象及演示图表数据符合技术汇报提纲。')
                else:
                    pending=next((s for s in slides if not s['review'].get('automatic_passed')),None)
                    if pending:name,args='pptx_check_slide',dict(presentation_id='project-report',slide_id=pending['slide_id'],content_notes='核对总结、后续安排和可编辑对象，示例数据均已明确标注。')
                    else:return ChatResult(generations=[ChatGeneration(message=AIMessage(content='12 页可编辑汇报已完成自动和内容检查；视觉审查仍待实际查看截图。'))])
            if operation:name,args='pptx_apply_one',dict(presentation_id='project-report',operation_id=opid,base_revision=rev,**operation)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[dict(name=name,args=args,id=str(time.time_ns()),type='tool_call')]))])
agent.llm.build_chat_model=lambda *a,**k:Model()
agent.runner.build_chat_model=lambda *a,**k:Model()
if __name__=='__main__':
    import uvicorn
    print('PPTX_E2E_DATA='+str(d.DATA),flush=True);uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('PPTX_E2E_PORT','8084')),log_level='warning')
