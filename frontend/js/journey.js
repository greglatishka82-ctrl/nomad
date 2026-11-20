(()=>{'use strict';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const skillWord=n=>{let v=n%100;return v>=11&&v<=14?'навыков':n%10===1?'навык':n%10>=2&&n%10<=4?'навыка':'навыков'};
const levels=['Не оценивался','С помощью инструктора','С подсказками','Самостоятельно, с ошибками','Правильно и стабильно','Уверенно и самостоятельно'];
const feelings={hard:'Было сложно',clear:'Стало понятнее',confident:'Чувствую уверенность'};
const TG='https://t.me/green_time_drive_bot';
const SITE='https://greentime.luxe';
const WA='https://wa.me/77078810848?text='+encodeURIComponent('Здравствуйте! Хочу записаться на занятие по вождению в GREENTIME.');
const CTA_HTML='<a class="button primary" href="'+TG+'" target="_blank" rel="noopener">Записаться в Telegram ↗</a><a class="button wa" href="'+WA+'" target="_blank" rel="noopener">Записаться в WhatsApp</a>';
const txLabel=v=>v==='automatic'?' · КПП автомат':v==='manual'?' · КПП механика':'';
let data,extras,achievement,lastTrigger,formatReplies;
const date=s=>{const d=new Date(s+'T12:00:00');return isNaN(d)?s:d.toLocaleDateString('ru-RU',{day:'numeric',month:'long'})};
function flatten(h){return Object.entries(h.ratings||{}).flatMap(([cat,vs])=>{const c=data.categories.find(c=>c.id===cat);return c?Object.entries(vs).filter(([i,v])=>c.skills[i]&&Number.isInteger(v)&&v>=1&&v<=5).map(([i,v])=>({key:cat+':'+i,name:c.skills[i].name,value:v})):[]})}
function compareSkills(){const map=new Map;[...data.history].reverse().forEach(h=>flatten(h).forEach(s=>{const p=map.get(s.key);if(p){p.last=s.value;p.lastDate=h.date;p.count++}else map.set(s.key,{...s,first:s.value,last:s.value,firstDate:h.date,lastDate:h.date,count:1})}));return [...map.values()].filter(s=>s.count>1).sort((a,b)=>(b.last-b.first)-(a.last-a.first))}
function recap(icon,label,title,desc){return '<article class="recap"><span class="recap-icon" aria-hidden="true">'+icon+'</span><p class="eyebrow">'+esc(label)+'</p><h3>'+esc(title)+'</h3><p>'+esc(desc)+'</p></article>'}
function render(){
const firstName=data.name?.trim().split(/\s+/)[0]||'Друг';$('greeting').textContent=firstName+', это ваш путь.';
$('visits').textContent='Завершено занятий: '+data.completed_visits+' · Оценено навыков: '+data.assessed_skills;
const comparisons=compareSkills(),growth=comparisons.filter(s=>s.last>s.first);
$('heroLead').textContent=growth.length?'Ваши занятия складываются в результат. Здесь видно, что уже получается лучше и над чем вы работаете сейчас.':'Здесь будет ваша история: первые попытки, новые навыки и маленькие победы после каждого занятия.';
const upcoming=extras.upcoming;
renderNextStrip(upcoming);
const last=data.history[0],rated=last?flatten(last):[],prior=new Map;[...data.history.slice(1)].reverse().forEach(h=>flatten(h).forEach(s=>prior.set(s.key,s.value)));
const improved=rated.filter(s=>prior.has(s.key)&&s.value>prior.get(s.key)).sort((a,b)=>(b.value-prior.get(b.key))-(a.value-prior.get(a.key)));
const best=[...rated].sort((a,b)=>b.value-a.value)[0],focus=[...rated].filter(s=>s.value<4).sort((a,b)=>a.value-b.value)[0];
$('lastDate').textContent=last?date(last.date)+' · '+last.time:'Первый урок впереди';
$('recap').innerHTML=recap('✓','Получается',best?best.name:'Здесь появятся ваши сильные стороны',best?levels[best.value]:'После завершённого урока инструктор сможет оценить навыки.')+recap('↗','Изменилось',improved[0]?improved[0].name:rated.length?'Начало вашей истории':'Результаты впереди',improved[0]?levels[prior.get(improved[0].key)]+' → '+levels[improved[0].value]:rated.length?'Новые оценки помогут увидеть изменения. За этот урок роста по повторно оценённым навыкам пока не отмечено.':'Сравнение появится, когда будут повторные оценки.')+recap('◎','Стоит повторить',focus?focus.name:rated.length?'Продолжайте закреплять навыки':'Всё в своём темпе',focus?levels[focus.value]+'. Обсудите с инструктором, чему уделить время.':rated.length?'Среди оценённых на этом уроке навыков нет оценок ниже 4.':'Инструктор поможет определить первый ориентир.');
$('instructorNote').innerHTML='<span class="quote" aria-hidden="true">“</span><div><p>'+esc(last?.comment||'После урока здесь появится комментарий инструктора, если он его оставит.')+'</p><small>'+esc(last?.instructor||'GREENTIME')+(last?' · '+esc(date(last.date)):'')+'</small></div>';
const history=[...data.history].reverse();
const completedStop=(h,i)=>'<button class="stop '+(i===history.length-1?'latest':'')+'" data-lesson="'+(history.length-1-i)+'" aria-label="Занятие '+(i+1)+', '+esc(date(h.date))+'"><span class="stop-node">'+(i+1)+'</span><small>'+esc(date(h.date))+'</small><strong>'+(i>=9?'Дополнительное занятие':i===history.length-1?'Последнее занятие':'Занятие '+(i+1))+'</strong><span class="stop-caption">'+esc(h.instructor)+'<br>'+flatten(h).length+' навыков оценено ↗</span></button>';
const plannedStops=Array.from({length:Math.max(0,9-history.length)},(_,i)=>{
 const n=history.length+i+1,next=i===0;
 const tag=next?'a':'div';
 return '<'+tag+' class="stop planned"'+(next?' href="'+TG+'" target="_blank" rel="noopener"':'')+'><span class="stop-node">'+n+'</span><small>'+(next&&upcoming?esc(date(upcoming.date)):'Впереди')+'</small><strong>Занятие '+n+'</strong><span class="stop-caption">'+(next?(upcoming?esc(upcoming.time)+' · '+(upcoming.status==='pending'?'Ожидает подтверждения':'Запланировано'):'Записаться в Telegram ↗'):'Практика с инструктором')+'</span></'+tag+'>';
}).join('');
const examStop='<div class="stop planned exam-stop"><span class="stop-node">10</span><small>Ориентир · по готовности</small><strong>Сдача экзамена</strong><span class="stop-caption">После подготовки и отработки элементов</span></div>';
$('route').innerHTML=history.slice(0,9).map(completedStop).join('')+plannedStops+examStop+history.slice(9).map((h,i)=>completedStop(h,i+9)).join('');

const currentGoal=last?extras.notes[String(last.booking_id)]?.goal:'';
$('goal').innerHTML='<span class="goal-icon" aria-hidden="true">◎</span><p class="eyebrow">СЛЕДУЮЩАЯ ЦЕЛЬ</p><h2>'+esc(currentGoal||'Определите ориентир вместе с инструктором')+'</h2><p class="muted">'+(currentGoal?'Один понятный шаг, которому можно уделить внимание на следующем занятии.':'Инструктор может оставить здесь цель после урока. Она поможет сосредоточиться на том, что сейчас важно именно вам.')+'</p><span class="goal-from">'+(currentGoal?'Цель от инструктора · '+esc(last.instructor):'Персональный план, без спешки')+'</span>';
$('reflectionContext').textContent=last?'Занятие '+date(last.date)+'. Это ваша самооценка — она не меняет оценки навыков. Инструктор увидит её в истории.':'После первого завершённого занятия здесь можно будет оставить впечатление и вопрос инструктору.';
formatReplies=n=>(n?.replies||[]).map(r=>'<div class="feedback-reply"><strong>Ответ инструктора · '+esc(r.instructor)+'</strong><p>'+esc(r.body)+'</p><small>На ваше сообщение: '+esc(r.question)+'</small></div>').join('');
$('instructorReplies').innerHTML=last?formatReplies(extras.notes[String(last.booking_id)]):'';
$('reflectionForm').hidden=!last;
if(last){const n=extras.notes[String(last.booking_id)]||{};const input=document.querySelector('input[name="feeling"][value="'+(Object.hasOwn(feelings,n.feeling)?n.feeling:'hard')+'"]');if(n.feeling)input.checked=true;$('reflectionText').value=n.reflection||'';if(n.feeling)$('reflectionStatus').textContent='Ваше впечатление сохранено. Его можно обновить.'}
$('coverage').textContent=data.assessed_skills+' из '+data.total_skills+' оценено';
const icons={city:'↗',site:'⊞',advanced:'◇',defensive:'◈',drift:'⌁',vehicle:'⚙'};
$('skillMap').innerHTML=data.categories.map(c=>{const assessed=c.skills.filter(s=>s.rating!=null),strong=assessed.filter(s=>s.rating>=4).length;return '<details class="skill-group"><summary><span class="category-icon" aria-hidden="true">'+(icons[c.id]||'◎')+'</span><span class="expand" aria-hidden="true">+</span><h3>'+esc(c.shortTitle)+'</h3><p class="skill-caption">'+(assessed.length?strong+' из '+assessed.length+' оценённых — стабильно или уверенно':'Ещё не оценивалось')+'</p><div class="segments" aria-hidden="true">'+Array.from({length:5},(_,i)=>'<span class="'+(assessed.length&&i<Math.round(assessed.reduce((n,s)=>n+s.rating,0)/assessed.length)?'filled':'')+'"></span>').join('')+'</div></summary><div class="skill-list">'+c.skills.map(s=>'<div class="skill-row"><strong>'+esc(s.name)+'</strong><span class="'+(s.rating>=4?'mastered':'')+'">'+esc(levels[s.rating??0])+(s.rating!=null?' · '+s.rating+'/5':'')+'</span></div>').join('')+'</div></details>'}).join('');
$('comparison').innerHTML=comparisons.length?comparisons.map(s=>'<div class="comparison-row"><strong>'+esc(s.name)+'</strong><div class="before"><small>'+esc(date(s.firstDate))+'</small>'+esc(levels[s.first])+'</div><span class="arrow" aria-hidden="true">→</span><div class="after '+(s.last<s.first?'needs-practice':'')+'"><small>'+esc(date(s.lastDate))+'</small>'+esc(levels[s.last])+'</div></div>').join(''):'<p class="empty-state">Пока недостаточно повторных оценок. Когда инструктор оценит один и тот же навык на разных занятиях, здесь появится сравнение.</p>';
$('scoreDescription').textContent='Текущий результат: '+(data.score??'—')+' из 100. Рассчитан только по оценённым навыкам. Меняется в том числе при добавлении новых упражнений и не означает готовность к экзамену.';
$('scoreSeries').innerHTML=data.series.map(s=>'<div class="score-point">'+esc(date(s.date))+'<b>'+esc(s.score??'—')+' <small>/ 100</small></b></div>').join('')||'Оценок пока нет.';
const earned=data.categories.flatMap(c=>c.skills.filter(s=>s.rating>=4).map(s=>({...s,category:c.shortTitle}))).sort((a,b)=>b.rating-a.rating);
achievement=earned[0]||null;
renderAchievement();
$('status').hidden=true;$('content').hidden=false;
}
function openLesson(i,trigger){const h=data.history[i];if(!h)return;lastTrigger=trigger;const n=extras.notes[String(h.booking_id)]||{};$('lessonDetail').innerHTML='<h2>Занятие '+(data.history.length-i)+'</h2><p class="lesson-meta">'+esc(date(h.date))+' · '+esc(h.time)+' · '+esc(h.instructor)+'</p>'+flatten(h).map(s=>'<div class="lesson-skill"><strong>'+esc(s.name)+'</strong><span>'+esc(levels[s.value])+' · '+s.value+'/5</span></div>').join('')+(!flatten(h).length?'<p class="empty">Навыки на этом занятии не оценивались.</p>':'')+(h.comment?'<p class="lesson-comment">'+esc(h.comment)+'</p>':'')+(n.goal?'<p class="muted"><b>Цель после урока:</b> '+esc(n.goal)+'</p>':'')+(n.feeling?'<p class="muted"><b>Ваше впечатление:</b> '+esc(feelings[n.feeling])+'</p>':'')+(n.reflection?'<p class="lesson-comment">'+esc(n.reflection)+'</p>':'')+formatReplies(n);$('lessonDialog').showModal()}
$('route').onclick=e=>{const b=e.target.closest('[data-lesson]');if(b)openLesson(Number(b.dataset.lesson),b)};
$('closeLesson').onclick=()=>$('lessonDialog').close();$('lessonDialog').addEventListener('close',()=>lastTrigger?.focus());$('lessonDialog').onclick=e=>{if(e.target===$('lessonDialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close()}};
$('reflectionForm').onsubmit=async e=>{e.preventDefault();const btn=e.target.querySelector('button');btn.disabled=true;$('reflectionStatus').textContent='Сохраняем…';try{const body={feeling:new FormData(e.target).get('feeling'),reflection:$('reflectionText').value};const id=data.history[0].booking_id;const r=await fetch('/api/profile/reflection/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error(r.status===401?'Войдите в кабинет ещё раз.':'Не удалось сохранить. Попробуйте ещё раз.');extras.notes[String(id)]={...extras.notes[String(id)],...body};$('reflectionStatus').textContent='Сохранено. Инструктор увидит ваше впечатление в истории.'}catch(e){$('reflectionStatus').textContent=e.message}finally{btn.disabled=false}};
/* ---------- Следующее занятие: карточка и запись ---------- */
function renderNextStrip(upcoming){
  const el=$('upcoming');
  if(upcoming){
    el.innerHTML='<div class="next-card"><span class="next-icon" aria-hidden="true">↗</span><div class="next-card__body"><p class="eyebrow">СЛЕДУЮЩЕЕ ЗАНЯТИЕ</p><h3>'+esc(date(upcoming.date))+' в '+esc(upcoming.time)+'</h3><p>'+esc(upcoming.instructor||'GREENTIME')+txLabel(upcoming.transmission)+' · '+(upcoming.status==='pending'?'ожидает подтверждения':upcoming.status==='in_progress'?'занятие идёт':'подтверждено')+'</p></div><div class="next-card__actions"><a class="button primary" href="'+TG+'" target="_blank" rel="noopener">Мои записи ↗</a><a class="button wa" href="'+WA+'" target="_blank" rel="noopener">Написать в WhatsApp</a></div></div>';
  }else{
    el.innerHTML='<div class="next-card"><span class="next-icon" aria-hidden="true">✚</span><div class="next-card__body"><p class="eyebrow">СЛЕДУЮЩИЙ ШАГ</p><h3>Следующее занятие пока не записано</h3><p>Свободные окна есть на этой неделе. Оставьте заявку в Telegram или WhatsApp — подберём удобное время и инструктора.</p></div><div class="next-card__actions">'+CTA_HTML+'</div></div>';
  }
}

/* ---------- Блок маленьких побед и карточка прогресса ---------- */
function renderAchievement(){
  const el=$('achievement');
  if(!achievement){
    el.classList.add('achievement-panel--empty');
    el.innerHTML='<div><p class="eyebrow">МАЛЕНЬКИЕ ПОБЕДЫ — БОЛЬШОЙ ПУТЬ</p><h2>Ваш первый уверенный результат ещё впереди</h2><p>После первого занятия инструктор оценит навыки, и здесь появится личный прогресс, которым можно поделиться. А записаться на первое занятие можно прямо сейчас.</p></div><div class="achievement-panel__actions">'+CTA_HTML+'</div>';
    return;
  }
  el.classList.remove('achievement-panel--empty');
  el.innerHTML='<div><p class="eyebrow">МАЛЕНЬКИЕ ПОБЕДЫ — БОЛЬШОЙ ПУТЬ</p><h2>'+esc(achievement.name)+'</h2><p>'+esc(levels[achievement.rating])+'. Оценка инструктора: '+achievement.rating+' из 5. Соберите красивую карточку прогресса и поделитесь результатом — или скачайте карточку на память.</p></div><div class="achievement-panel__actions"><button class="button primary" id="openShare" type="button">Поделиться прогрессом</button><button class="button dark" id="saveAchievement" type="button">Скачать карточку ↓</button></div>';
  $('openShare').onclick=openShare;
  $('saveAchievement').onclick=async()=>{const btn=$('saveAchievement');btn.disabled=true;const old=btn.textContent;btn.textContent='Скачиваем…';try{await saveCard()}catch(e){}finally{btn.disabled=false;btn.textContent=old}};
}

let cardBlob=null,cardUrl=null,cardBusy=null;
function firstName(){return ((data&&data.name)||'').trim().split(/\s+/)[0]||''}
function shareMessage(){
  const ach=achievement?('Навык «'+achievement.name+'» — '+achievement.rating+' из 5 в автошколе GREENTIME. '):('Мой прогресс в автошколе GREENTIME: '+data.completed_visits+' занятий, '+data.assessed_skills+' навыков оценено. ');
  return (firstName()?firstName()+': ':'')+ach+'Учусь водить в Павлодаре. Записывайся тоже: '+SITE+' #GREENTIME #автошкола #Павлодар';
}
function roundRect(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath()}
function wrapText(c,text,x,y,maxWidth,lineHeight){const words=String(text).split(' ');let line='';for(const word of words){const test=line?line+' '+word:word;if(c.measureText(test).width>maxWidth&&line){c.fillText(line,x,y);line=word;y+=lineHeight}else line=test}if(line)c.fillText(line,x,y);return y}
function buildCardCanvas(){
  const W=1080,H=1350,canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;const c=canvas.getContext('2d');
  const g=c.createLinearGradient(0,0,W,H);g.addColorStop(0,'#0b1714');g.addColorStop(1,'#173127');c.fillStyle=g;c.fillRect(0,0,W,H);
  const rg=c.createRadialGradient(900,150,10,900,150,460);rg.addColorStop(0,'rgba(185,237,133,.18)');rg.addColorStop(1,'rgba(185,237,133,0)');c.fillStyle=rg;c.fillRect(380,0,700,650);
  c.fillStyle='#b9ed85';c.font='600 46px Unbounded';c.fillText('GREENTIME',80,124);
  c.fillStyle='#a8bfae';c.font='600 24px Manrope';c.fillText('АВТОШКОЛА · ПАВЛОДАР',80,172);
  c.fillStyle='#7f978a';c.font='700 21px Manrope';c.fillText('МОЙ ПУТЬ ЗА РУЛЁМ',80,226);
  const nm=firstName();
  if(nm){c.textAlign='right';c.fillStyle='#e8f2e4';c.font='600 26px Manrope';c.fillText(nm,1000,172);c.fillStyle='#7f978a';c.font='20px Manrope';c.fillText('ученик GREENTIME',1000,206);c.textAlign='left'}
  c.fillStyle='#ffffff';c.font='500 58px Unbounded';c.fillText('У меня',80,330);c.fillText('получается.',80,410);
  if(achievement){
    const bx=880,by=330,r=112;
    c.fillStyle='rgba(185,237,133,.14)';c.beginPath();c.arc(bx,by,r,0,Math.PI*2);c.fill();
    c.strokeStyle='rgba(185,237,133,.55)';c.lineWidth=3;c.stroke();
    c.textAlign='center';c.fillStyle='#b9ed85';c.font='600 64px Unbounded';c.fillText(achievement.rating+'/5',bx,by+18);
    c.fillStyle='#a8bfae';c.font='600 22px Manrope';c.fillText('ОЦЕНКА',bx,by+58);c.textAlign='left';
  }
  c.fillStyle='#d9ead8';c.font='600 42px Manrope';const y=wrapText(c,achievement?achievement.name:'Мой прогресс',80,500,780,56);
  if(achievement){
    c.fillStyle='#b9ed85';c.font='600 30px Manrope';c.fillText('Оценка инструктора · '+achievement.rating+' из 5',80,y+64);
    c.fillStyle='#a8bfae';c.font='25px Manrope';c.fillText(levels[achievement.rating],80,y+108);
  }
  const sc=(data.score==null||data.score==='')?'—':data.score;
  const stats=[['ЗАНЯТИЙ',String(data.completed_visits)],['НАВЫКОВ ОЦЕНЕНО',data.assessed_skills+' из '+data.total_skills],['РЕЗУЛЬТАТ',sc+' / 100']];
  const top=760,cardW=310,cardH=150;
  stats.forEach(([label,value],i)=>{const x=80+i*(cardW+20);c.fillStyle='rgba(255,255,255,.06)';c.strokeStyle='rgba(185,237,133,.22)';c.lineWidth=2;roundRect(c,x,top,cardW,cardH,20);c.fill();c.stroke();c.fillStyle='#8fa89a';c.font='700 20px Manrope';c.fillText(label,x+24,top+44);c.fillStyle='#ffffff';c.font='600 34px Unbounded';c.fillText(String(value),x+24,top+104)});
  c.fillStyle='#b9ed85';c.font='600 40px Unbounded';c.fillText('Практика меняет всё.',80,1024);
  c.strokeStyle='rgba(255,255,255,.14)';c.lineWidth=2;c.beginPath();c.moveTo(80,1080);c.lineTo(1000,1080);c.stroke();
  c.fillStyle='#e8f2e4';c.font='600 28px Manrope';c.fillText('Записывайся тоже → greentime.luxe',80,1132);
  c.fillStyle='#9db3a5';c.font='24px Manrope';c.fillText('Telegram: @green_time_drive_bot · Instagram: @autodrom.greentime',80,1174);
  c.fillStyle='#7f978a';c.font='20px Manrope';c.fillText('GREENTIME · Павлодар · Братьев Дюсембиновых 5',80,1212);
  c.fillStyle='rgba(255,255,255,.04)';c.fillRect(0,1246,W,104);
  c.strokeStyle='rgba(185,237,133,.45)';c.lineWidth=3;c.beginPath();c.moveTo(0,1246);c.lineTo(W,1246);c.stroke();
  c.fillStyle='#7f978a';c.font='600 20px Manrope';c.textAlign='center';c.fillText('ВАШ ТЕМП. ВАША ПРАКТИКА. ВАШ РЕЗУЛЬТАТ.',W/2,1302);c.textAlign='left';
  return canvas;
}
function getCardBlob(){
  if(cardBlob)return Promise.resolve(cardBlob);
  if(cardBusy)return cardBusy;
  cardBusy=(async()=>{await document.fonts.ready;const canvas=buildCardCanvas();const blob=await new Promise(r=>canvas.toBlob(r,'image/png'));if(!blob)throw Error('card');cardBlob=blob;if(cardUrl)URL.revokeObjectURL(cardUrl);cardUrl=URL.createObjectURL(blob);return blob})();
  return cardBusy;
}
async function saveCard(){
  await getCardBlob();
  const a=document.createElement('a');a.href=cardUrl;a.download='GREENTIME-прогресс.png';document.body.appendChild(a);a.click();a.remove();
}
async function shareFiles(text){
  const file=new File([cardBlob],'greentime-progress.png',{type:'image/png'});
  if(navigator.canShare&&navigator.canShare({files:[file]})){
    try{await navigator.share({files:[file],text});return true}catch(e){if(e&&e.name==='AbortError')return true}
    try{await navigator.share({files:[file]});return true}catch(e){if(e&&e.name==='AbortError')return true}
  }
  return false;
}
async function openShare(){
  const dlg=$('shareDialog');$('shareStatus').textContent='Собираем карточку…';$('sharePreview').removeAttribute('src');
  try{
    await getCardBlob();
    $('sharePreview').src=cardUrl;
    $('shareTg').href='https://t.me/share/url?url='+encodeURIComponent(SITE)+'&text='+encodeURIComponent(shareMessage());
    $('shareWa').href='https://wa.me/?text='+encodeURIComponent(shareMessage());
    $('shareStatus').textContent='';
    if(!dlg.open)dlg.showModal();
  }catch(e){$('shareStatus').textContent='Не удалось собрать карточку. Попробуйте ещё раз.'}
}
async function shareNative(){
  try{
    await getCardBlob();
    if(await shareFiles(shareMessage()))return;
    if(navigator.share){await navigator.share({title:'Мой прогресс в GREENTIME',text:shareMessage(),url:SITE});return}
    await saveCard();$('shareStatus').textContent='Карточка сохранена. Отправьте её в соцсеть вручную.';
  }catch(e){if(!e||e.name!=='AbortError')$('shareStatus').textContent='Не удалось открыть меню «Поделиться». Скачайте карточку и опубликуйте её вручную.'}
}
async function shareInstagram(){
  try{
    await getCardBlob();
    if(await shareFiles(shareMessage())){$('shareStatus').textContent='Выберите Instagram в открывшемся меню.';return}
    await saveCard();$('shareStatus').textContent='Карточка сохранена. Откройте Instagram и опубликуйте её в ленте или сторис.';
  }catch(e){if(!e||e.name!=='AbortError')$('shareStatus').textContent='Не удалось открыть Instagram. Скачайте карточку и опубликуйте её вручную.'}
}
$('closeShare').onclick=()=>$('shareDialog').close();
$('shareDialog').addEventListener('click',e=>{if(e.target===$('shareDialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close()}});
$('shareNative').onclick=shareNative;
$('shareIg').onclick=shareInstagram;
$('shareDownload').onclick=async()=>{const btn=$('shareDownload');btn.disabled=true;const old=btn.textContent;btn.textContent='Скачиваем…';try{await saveCard();$('shareStatus').textContent='Карточка сохранена на устройство.'}catch(e){$('shareStatus').textContent='Не удалось скачать карточку. Попробуйте ещё раз.'}finally{btn.disabled=false;btn.textContent=old}};
$('shareCopy').onclick=async()=>{try{await navigator.clipboard.writeText(SITE+'/?utm_source=share');$('shareStatus').textContent='Ссылка скопирована.'}catch(e){$('shareStatus').textContent='Скопируйте ссылку вручную: '+SITE}};
$('logout').onclick=async()=>{const r=await fetch('/api/profile/logout',{method:'POST'});if(r.ok)location.href='/';else alert('Не удалось выйти. Попробуйте ещё раз.')};
async function load(){try{const responses=await Promise.all([fetch('/api/profile/me'),fetch('/api/profile/journey')]);if(responses.some(r=>r.status===401)){location.replace('/?cabinet=1');return}if(responses.some(r=>!r.ok))throw Error('Не удалось загрузить ваш профиль. Попробуйте ещё раз.');[data,extras]=await Promise.all(responses.map(r=>r.json()));render()}catch(e){$('status').innerHTML='<p>'+esc(e.message)+'</p><button class="button dark" id="retry">Повторить</button>';$('retry').onclick=load}}load();
/* ---------- Статистика нажатий в кабинете ---------- */
function trackCabinetClick(label,target){
  try{
    const uid=()=>crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)+'-'+Math.random().toString(36).slice(2);
    let visitor=localStorage.getItem('gt_visitor');if(!visitor){visitor=uid();localStorage.setItem('gt_visitor',visitor)}
    let session=sessionStorage.getItem('gt_session');if(!session){session=uid();sessionStorage.setItem('gt_session',session)}
    const ua=navigator.userAgent||'';
    const device=/iPad|Tablet/i.test(ua)?'tablet':/Mobi|Android/i.test(ua)?'mobile':'desktop';
    const scr=(window.screen&&screen.width?screen.width:0)+'x'+(window.screen&&screen.height?screen.height:0);
    const data=JSON.stringify({visitor,session,kind:'click',path:'/profile.html',source:'',medium:'',campaign:'',referrer:'',device,browser:'',os:'',screen:scr,language:(navigator.language||'').slice(0,24),label:String(label||'').slice(0,100),target:String(target||'button').slice(0,100),value:0});
    if(navigator.sendBeacon)navigator.sendBeacon('/api/site-traffic/event',new Blob([data],{type:'application/json'}));
    else fetch('/api/site-traffic/event',{method:'POST',headers:{'Content-Type':'application/json'},body:data,keepalive:true}).catch(()=>{});
  }catch(e){}
}
document.addEventListener('click',e=>{
  const el=e.target.closest('a,button');if(!el)return;
  if(el.hasAttribute('download'))return;
  if(el.tagName==='A'&&(el.getAttribute('href')||'').startsWith('blob:'))return;
  let target='button';
  if(el.tagName==='A'){
    try{const u=new URL(el.getAttribute('href')||'',location.href);target=/wa\.me|whatsapp/i.test(u.hostname)?'whatsapp':/t\.me|telegram/i.test(u.hostname)?'telegram':u.hostname===location.hostname?'internal':'external'}catch(_){}
  }
  let label=(el.getAttribute('aria-label')||el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
  if(!label)label=el.id||'Кнопка';
  if(label==='Закрыть'||label==='×')return;
  if(el.closest('#shareDialog'))label='Карточка: '+label;
  trackCabinetClick(label,target);
},{capture:true,passive:true});})();