(function(){
 'use strict';
 var select=document.getElementById('site-language'), lang='ru', dict={}, originals=new WeakMap(), attrs=new WeakMap(), observer, busy=false;
 try { var saved=localStorage.getItem('greentime-preview-language'); if(['ru','kk','en'].indexOf(saved)>=0)lang=saved; } catch(e){}
 select.value=lang; document.documentElement.lang=lang;
 function norm(s){return s.replace(/\s+/g,' ').trim();}
 function tr(s){var key=norm(s);if(lang==='ru'||!key)return s;var value=(dict[lang]||{})[key];
  if(!value){var hours=key.match(/^(\d+) час(?:а|ов)?$/);if(hours)value=lang==='kk'?hours[1]+' сағат':hours[1]+(hours[1]==='1'?' hour':' hours');
  var saving=key.match(/^скидка ([\d ]+) ₸$/);if(saving)value=(lang==='kk'?'жеңілдік ':'discount ')+saving[1]+' ₸';
  var experience=key.match(/^Стаж: (\d+) лет$/);if(experience)value=lang==='kk'?'Еңбек өтілі: '+experience[1]+' жыл':'Experience: '+experience[1]+' years';}
  return value?s.replace(/^(\s*)[\s\S]*?(\s*)$/,function(_,a,b){return a+value+b}):s;
 }
 window.greentimeTranslate=tr;
 function skip(node){var p=node.parentElement;return !p||!!p.closest('script,style,option,.msg--user,[data-no-translate]');}
 function textNode(n,changed){if(skip(n))return;var rec=originals.get(n);if(!rec||changed&&n.nodeValue!==rec.rendered)rec={source:n.nodeValue};var value=tr(rec.source);rec.rendered=value;originals.set(n,rec);if(n.nodeValue!==value)n.nodeValue=value;}
 function element(el){if(el.closest('script,style,[data-no-translate]'))return;['aria-label','placeholder','title','alt','content'].forEach(function(a){if(!el.hasAttribute(a))return;if(a==='content'&&!el.matches('meta[name="description"],meta[property="og:title"],meta[property="og:description"]'))return;var v=el.getAttribute(a),m=attrs.get(el)||{},rec=m[a];if(!rec||v!==rec.rendered)rec={source:v};var next=tr(rec.source);rec.rendered=next;m[a]=rec;attrs.set(el,m);if(v!==next)el.setAttribute(a,next)});}
 function walk(root){if(root.nodeType===3){textNode(root,true);return}if(root.nodeType!==1&&root.nodeType!==9)return;if(root.nodeType===1)element(root);var w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);while(w.nextNode()){var n=w.currentNode;if(n.nodeType===3)textNode(n,true);else element(n)}}
 function observe(){observer.observe(document.documentElement,{childList:true,characterData:true,subtree:true,attributes:true,attributeFilter:['aria-label','placeholder','title','alt','content']})}
 function apply(){observer.disconnect();document.documentElement.lang=lang;walk(document.documentElement);observe()}
 observer=new MutationObserver(function(changes){if(busy)return;busy=true;observer.disconnect();changes.forEach(function(change){if(change.type==='characterData')textNode(change.target,true);else if(change.type==='attributes')element(change.target);else change.addedNodes.forEach(walk)});observe();busy=false});
 select.addEventListener('change',function(){lang=select.value;try{localStorage.setItem('greentime-preview-language',lang)}catch(e){}apply()});
 fetch('/js/i18n-dictionary.json?v=contacts6').then(function(r){if(!r.ok)throw Error('dictionary');return r.json()}).then(function(d){dict=d;apply()}).catch(function(){apply()});
})();