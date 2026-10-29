export async function api(path,options={}) {
 const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)})
 if(!response.ok){const value=await response.json().catch(()=>({}));throw new Error(typeof value.detail==='string'?value.detail:'Feature request failed')}
 return response.json()
}
