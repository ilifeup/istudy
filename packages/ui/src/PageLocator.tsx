import React, { useState } from 'react';
import { PREVIEW_PAGES, previewAllowed, previewByRoute } from './previewRegistry';

type Props={ route:string; role:string; server:boolean; navigate:(route:string)=>void; };
export function PageLocator({route,role,server,navigate}:Props):any {
  const [expanded,setExpanded]=useState(false);
  const page=previewByRoute(route,server);
  if(!page) return null;
  const superUser=role==='super';
  const options=PREVIEW_PAGES.filter(p=>previewAllowed(p,role,server)&&!p.detail && (server || !p.serverOnly));
  const label=page.id;
  const copy=async()=>{
    const details=[`iStudy · ${server?'Server':'Client'}`,`${page.id}｜${page.label}`,`頁面：${page.route}`];
    if(server) details.push(`來源：${page.source}`);
    try { await navigator.clipboard.writeText(details.join('\n')); }
    catch { const el=document.createElement('textarea');el.value=details.join('\n');document.body.append(el);el.select();document.execCommand('copy');el.remove(); }
  };
  return <div aria-label="頁碼定位" style={{position:'fixed',right:12,bottom:42,zIndex:9000,fontSize:12,maxWidth:'min(94vw,440px)',background:'#f5f7fa',color:'#172b45',border:'1px solid #aebbc9',borderRadius:7,padding:'4px 7px',boxShadow:'0 1px 5px #0002'}}>
    <span title={page.label}>{label}</span>
    {superUser && <button type="button" aria-label="展開頁碼工具" onClick={()=>setExpanded(!expanded)} style={{marginLeft:6}}>{expanded?'收合':'選單'}</button>}
    {superUser && expanded && <div style={{display:'flex',flexWrap:'wrap',gap:6,paddingTop:7}}>
      <select aria-label="選擇頁面" value={options.some(p=>p.route===route)?route:''} onChange={e=>{if(e.target.value)navigate(e.target.value)}}><option value="">選擇頁面</option>{options.map(p=><option key={p.id} value={p.route}>{p.id}｜{p.label}</option>)}</select>
      <button type="button" onClick={()=>void copy()}>複製定位</button>
      {server && <small title="來源路徑">{page.source}</small>}
    </div>}
  </div>;
}
