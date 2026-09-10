import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpen, CheckCircle2, ChevronDown, CircleHelp, FileText, GraduationCap, LoaderCircle,
  Maximize2, MessageSquareText, Mic, Plus, RefreshCw, RotateCcw, Save, Send, Sparkles, Square, Trash2, UserPlus, X, XCircle,
} from 'lucide-react';
import {
  addLearningAsset, addLearningMember, createLearningCourse, createLearningQuestion,
  createSession, deleteLearningArtifact, deleteLearningCourse, getLearningCourse, getSession,
  listLearningCourses, listLearningDocuments, removeLearningAsset, removeLearningMember,
  saveLearningArtifact, saveLearningCurriculum, saveSessionMessages, streamChat, transcribeVoice,
  updateLearningCourse, updateLearningQuestion,
} from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';

const TABS = [
  ['overview', 'Overview'], ['curriculum', 'Curriculum'], ['content', 'Course Content'],
  ['tutor', 'AI Tutor'], ['study', 'Study Tools'], ['questions', 'Teacher / Advisor'],
];
const TUTOR_LANGUAGES = [
  ['auto', 'Auto'], ['en-US', 'English'], ['bn-BD', 'Bangla'],
  ['hi-IN', 'Hindi'], ['es-ES', 'Spanish'], ['ar-SA', 'Arabic'],
];
const tutorLanguageName=code=>TUTOR_LANGUAGES.find(([value])=>value===code)?.[1]||'selected language';
const supportedRecordingType=()=>{
  if(typeof window==='undefined'||!window.MediaRecorder?.isTypeSupported)return '';
  return ['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg;codecs=opus'].find(type=>window.MediaRecorder.isTypeSupported(type))||'';
};
const STUDY_PROMPTS = {
  summary: 'Create a concise lesson summary covering the central ideas and important evidence in the selected course content.',
  study_guide: 'Create a structured study guide with learning objectives, major topics, explanations, examples, and review checkpoints.',
  key_concepts: `Identify the key concepts using only the selected course content. Format every concept in readable Markdown with this structure:
## Concept name
**Definition:** A clear explanation.
**Why it matters:** Its significance in the course.
**Review question:** A question that checks understanding.
**Answer:** A concise grounded answer.
Use separate sections, short paragraphs, and source citations where available.`,
  flashcards: `Create exactly 12 concise flashcards based only on the selected course content. Use this exact plain-text format for every card, with no table:
FLASHCARD 1
QUESTION: A clear question
ANSWER: A concise grounded answer
Continue through FLASHCARD 12. Keep each answer focused enough to review as a short tutor response.`,
  practice_questions: `Create exactly 6 grounded practice questions that progress from recall to application. Return one compact JSON object only, without Markdown, citations outside JSON, commentary, or code fences, using this exact structure:
{"schema_version":1,"instructions":"Select every correct answer, then submit each question for immediate feedback.","questions":[{"id":"q1","question":"Question text","options":[{"id":"A","text":"Option text","correct":true},{"id":"B","text":"Option text","correct":false},{"id":"C","text":"Option text","correct":false},{"id":"D","text":"Option text","correct":false}],"explanation":"Explain why the correct answer or answers are correct using the course material."}]}
Every question must have exactly four distinct options labeled A, B, C, and D. One or more options may be correct. Vary the number of correct answers across the quiz. Keep every option under 18 words and every explanation to one concise sentence. Escape quotes inside JSON strings. Do not include facts that are unsupported by the selected course content.`,
};

const PRACTICE_RETRY_PROMPT = `Create exactly 4 concise multiple-choice practice questions from the selected course material. Return valid compact JSON only, with no Markdown or text before or after it. Use schema_version 1 and a questions array. Every question needs id, question, exactly four options labeled A through D, at least one option with correct true, and a one-sentence explanation. Keep option text under 15 words. One or more answers may be correct.`;

export default function LearningPanel({ activeWorkspace = null, onClose }) {
  const mobile = useMobile();
  const workspaceId = activeWorkspace?.id || '';
  const [courses, setCourses] = useState([]);
  const [course, setCourse] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [tab, setTab] = useState('overview');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = async (preferredId = '') => {
    if (!workspaceId) return;
    setBusy(true); setError('');
    try {
      const [courseRows, docRows] = await Promise.all([listLearningCourses(workspaceId), listLearningDocuments(workspaceId)]);
      setCourses(courseRows || []); setDocuments(docRows || []);
      const id = preferredId === null ? courseRows?.[0]?.id : (preferredId || course?.id || courseRows?.[0]?.id);
      setCourse(id ? await getLearningCourse(id) : null);
    } catch (e) { setError(e.message || String(e)); }
    finally { setBusy(false); }
  };
  useEffect(() => { setCourse(null); setCourses([]); load(null); }, [workspaceId]);

  const selectCourse = async id => {
    setBusy(true); setError('');
    try { setCourse(await getLearningCourse(id)); }
    catch (e) { setError(e.message || String(e)); }
    finally { setBusy(false); }
  };
  const mutate = async operation => {
    setBusy(true); setError('');
    try { const result = await operation(); if (course?.id) setCourse(await getLearningCourse(course.id)); return result; }
    catch (e) { setError(e.message || String(e)); throw e; }
    finally { setBusy(false); }
  };

  return <div style={s.overlay} role="dialog" aria-modal="true" aria-label="Learning Intelligence">
    <section style={{...s.panel, ...(mobile ? s.panelMobile : {})}}>
      <header style={s.header}>
        <div style={{minWidth:0}}><h2 style={s.title}><GraduationCap size={19}/> Learning Intelligence</h2><div style={s.subtitle}>Grounded learning across documents, recordings, and video</div></div>
        <div style={s.headerActions}>
          <button style={s.iconBtn} onClick={()=>load(course?.id)} title="Refresh"><RefreshCw size={16}/></button>
          <button style={s.iconBtn} onClick={onClose} title="Close"><X size={18}/></button>
        </div>
      </header>
      {!workspaceId ? <Empty title="Select a workspace" text="Learning courses are governed by a DocIntel workspace. Select or create one first."/> : <>
        <div style={s.courseBar}>
          <label style={s.inlineField}><span>Course</span><select value={course?.id || ''} onChange={e=>selectCourse(e.target.value)}><option value="">Create or select a course</option>{courses.map(item=><option key={item.id} value={item.id}>{item.course_code ? `${item.course_code} · ` : ''}{item.title}</option>)}</select></label>
          <CreateCourse workspaceId={workspaceId} busy={busy} onCreate={payload=>mutate(async()=>{const created=await createLearningCourse(payload); await load(created.id); return created;})}/>
          {course && <span style={s.persona}>{course.my_persona || 'member'}</span>}
        </div>
        {error && <div style={s.error}>{error}</div>}
        {busy && <div style={s.loading}><LoaderCircle size={15} className="spin"/> Updating learning workspace...</div>}
        {!course ? <Empty title="Build your first course" text="Create a course, attach processed workspace content, and begin grounded learning."/> : <>
          {mobile ? <select style={s.mobileTabs} value={tab} onChange={e=>setTab(e.target.value)}>{TABS.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select> : <nav style={s.tabs}>{TABS.map(([key,label])=><button key={key} style={{...s.tab, ...(tab===key?s.tabOn:{})}} onClick={()=>setTab(key)}>{label}</button>)}</nav>}
          <main style={s.body}>
            {tab==='overview' && <Overview course={course} setCourse={setCourse} mutate={mutate} onDeleted={async()=>{await deleteLearningCourse(course.id);setCourse(null);await load();}}/>} 
            {tab==='curriculum' && <Curriculum course={course} mutate={mutate}/>} 
            {tab==='content' && <CourseContent course={course} documents={documents} mutate={mutate}/>} 
            {tab==='tutor' && <Tutor course={course} workspaceId={workspaceId}/>} 
            {tab==='study' && <StudyTools course={course} workspaceId={workspaceId} mutate={mutate}/>} 
            {tab==='questions' && <Questions course={course} mutate={mutate}/>} 
          </main>
        </>}
      </>}
    </section>
  </div>;
}

function CreateCourse({workspaceId,busy,onCreate}) {
  const [open,setOpen]=useState(false); const [title,setTitle]=useState('');
  const submit=async()=>{if(!title.trim())return;await onCreate({workspace_id:workspaceId,title:title.trim()});setTitle('');setOpen(false)};
  return <div style={{display:'flex',gap:6,alignItems:'end'}}>{open&&<label style={s.inlineField}><span>New course title</span><input autoFocus value={title} onChange={e=>setTitle(e.target.value)} onKeyDown={e=>e.key==='Enter'&&submit()}/></label>}<button style={s.primary} disabled={busy} onClick={()=>open?submit():setOpen(true)}><Plus size={15}/>{open?'Create':'New course'}</button></div>;
}

function Overview({course,setCourse,mutate,onDeleted}) {
  const [member,setMember]=useState({email:'',persona:'student'});
  const save=()=>mutate(()=>updateLearningCourse(course.id,{title:course.title,course_code:course.course_code||'',semester:course.semester||'',description:course.description||'',instructor_name:course.instructor_name||'',objectives:String((course.objectives||[]).join('\n')).split('\n').filter(Boolean)}));
  return <div style={s.scroll}><Section title="Course profile" icon={<BookOpen size={16}/>}><div style={s.grid}>
    <Field label="Course title"><input value={course.title||''} onChange={e=>setCourse({...course,title:e.target.value})}/></Field>
    <Field label="Course code"><input value={course.course_code||''} onChange={e=>setCourse({...course,course_code:e.target.value})}/></Field>
    <Field label="Semester"><input value={course.semester||''} onChange={e=>setCourse({...course,semester:e.target.value})}/></Field>
    <Field label="Instructor"><input value={course.instructor_name||''} onChange={e=>setCourse({...course,instructor_name:e.target.value})}/></Field>
    <Field label="Description" wide><ExpandableEditor title="Course description" value={course.description||''} onChange={value=>setCourse({...course,description:value})}/></Field>
    <Field label="Learning objectives (one per line)" wide><ExpandableEditor title="Learning objectives" value={(course.objectives||[]).join('\n')} onChange={value=>setCourse({...course,objectives:value.split('\n')})}/></Field>
  </div>{course.can_manage&&<button style={s.primary} onClick={save}><Save size={15}/>Save course</button>}</Section>
  <Section title="Course members" icon={<UserPlus size={16}/>}><div style={s.addRow}><input placeholder="Member email" value={member.email} onChange={e=>setMember({...member,email:e.target.value})}/><select value={member.persona} onChange={e=>setMember({...member,persona:e.target.value})}>{['student','teacher','advisor','admin'].map(x=><option key={x}>{x}</option>)}</select><button style={s.secondary} disabled={!course.can_manage||!member.email} onClick={async()=>{await mutate(()=>addLearningMember(course.id,member));setMember({...member,email:''})}}><Plus size={15}/>Enroll</button></div>
  <div style={s.cards}>{(course.members||[]).map(item=><article style={s.card} key={item.user_id}><div><strong>{item.full_name||item.email}</strong><small>{item.email} · {item.persona}</small></div>{course.can_manage&&item.user_id!==course.created_by&&<button style={s.dangerIcon} onClick={()=>mutate(()=>removeLearningMember(course.id,item.user_id))} title="Remove"><Trash2 size={15}/></button>}</article>)}</div></Section>
  {course.can_manage&&<button style={s.danger} onClick={()=>confirm('Delete this course and its learning records? Source documents will remain in DocIntel.')&&onDeleted()}><Trash2 size={15}/>Delete course</button>}</div>;
}

function Curriculum({course,mutate}) {
  const [modules,setModules]=useState(course.modules||[]); useEffect(()=>setModules(course.modules||[]),[course.id,course.modules]);
  const update=(mi,key,value)=>setModules(rows=>rows.map((row,i)=>i===mi?{...row,[key]:value}:row));
  const lesson=(mi,li,key,value)=>setModules(rows=>rows.map((row,i)=>i===mi?{...row,lessons:(row.lessons||[]).map((x,j)=>j===li?{...x,[key]:value}:x)}:row));
  return <div style={s.scroll}><div style={s.toolbar}><p>Organize a semester into modules and lessons. Course content remains reusable across the curriculum.</p>{course.can_manage&&<><button style={s.secondary} onClick={()=>setModules([...modules,{title:'',description:'',lessons:[]}])}><Plus size={15}/>Module</button><button style={s.primary} onClick={()=>mutate(()=>saveLearningCurriculum(course.id,modules))}><Save size={15}/>Save</button></>}</div>
  {modules.map((module,mi)=><Section key={mi} title={`Module ${mi+1}`}><div style={s.grid}><Field label="Title"><ExpandableEditor compact title={`Module ${mi+1} title`} placeholder="Module title" value={module.title||''} disabled={!course.can_manage} onChange={value=>update(mi,'title',value)}/></Field><Field label="Description" wide><ExpandableEditor title={`Module ${mi+1} description`} value={module.description||''} disabled={!course.can_manage} onChange={value=>update(mi,'description',value)}/></Field></div>
  {(module.lessons||[]).map((item,li)=><div style={s.lesson} key={li}><span style={s.lessonNumber}>{li+1}</span><div style={s.lessonFields}><Field label="Lesson title"><ExpandableEditor compact title={`Module ${mi+1}, lesson ${li+1} title`} placeholder="Lesson title" value={item.title||''} disabled={!course.can_manage} onChange={value=>lesson(mi,li,'title',value)}/></Field><Field label="Lesson description"><ExpandableEditor compact title={`Module ${mi+1}, lesson ${li+1} description`} placeholder="Lesson description" value={item.description||''} disabled={!course.can_manage} onChange={value=>lesson(mi,li,'description',value)}/></Field></div>{course.can_manage&&<button style={s.dangerIcon} onClick={()=>update(mi,'lessons',module.lessons.filter((_,j)=>j!==li))}><Trash2 size={14}/></button>}</div>)}
  {course.can_manage&&<div style={s.inlineActions}><button style={s.secondary} onClick={()=>update(mi,'lessons',[...(module.lessons||[]),{title:'',description:''}])}><Plus size={14}/>Lesson</button><button style={s.dangerIcon} onClick={()=>setModules(modules.filter((_,i)=>i!==mi))}><Trash2 size={14}/></button></div>}</Section>)}
  {!modules.length&&<Empty title="No curriculum yet" text="Add the first module to create the learning path."/>}</div>;
}

function CourseContent({course,documents,mutate}) {
  const [documentId,setDocumentId]=useState('');
  const [mapping,setMapping]=useState({module_id:'',lesson_id:''});
  useEffect(()=>{setDocumentId('');setMapping({module_id:'',lesson_id:''})},[course.id]);
  const attached=new Set((course.assets||[]).map(x=>x.document_id));
  const available=documents.filter(x=>!attached.has(x.id));
  const attach=async()=>{
    await mutate(()=>addLearningAsset(course.id,{document_id:documentId,module_id:mapping.module_id||null,lesson_id:mapping.lesson_id||null}));
    setDocumentId('');
  };
  return <div style={s.scroll}><Section title="Attach workspace content" icon={<FileText size={16}/>}><div style={s.mappingRow}><Field label="Content"><select value={documentId} onChange={e=>setDocumentId(e.target.value)}><option value="">Choose a document, audio file, or video</option>{available.map(doc=><option key={doc.id} value={doc.id}>{doc.original_name} · {doc.status}</option>)}</select></Field><MappingSelectors course={course} value={mapping} onChange={setMapping}/><button style={s.primary} disabled={!course.can_manage||!documentId} onClick={attach}><Plus size={15}/>Attach</button></div></Section>
  <div style={s.assetGrid}>{(course.assets||[]).map(asset=><AssetMappingCard key={asset.id} asset={asset} course={course} mutate={mutate}/>)}</div>
  {!course.assets?.length&&<Empty title="No course content" text="Attach processed workspace files. Embedded assets become available to the tutor and study tools."/>}</div>;
}

function AssetMappingCard({asset,course,mutate}) {
  const [mapping,setMapping]=useState({module_id:asset.module_id||'',lesson_id:asset.lesson_id||''});
  useEffect(()=>setMapping({module_id:asset.module_id||'',lesson_id:asset.lesson_id||''}),[asset.module_id,asset.lesson_id]);
  const changed=mapping.module_id!==(asset.module_id||'')||mapping.lesson_id!==(asset.lesson_id||'');
  const save=()=>mutate(()=>addLearningAsset(course.id,{document_id:asset.document_id,module_id:mapping.module_id||null,lesson_id:mapping.lesson_id||null,title:asset.title||asset.original_name}));
  return <article style={s.assetMapping}>
    <header style={s.assetHeader}><div style={s.assetIcon}>{asset.file_type==='video'?'▶':'▤'}</div><div style={{minWidth:0,flex:1}}><strong style={s.ellipsis}>{asset.title||asset.original_name}</strong><small>{asset.doc_type||asset.file_type} · {asset.status} · {asset.chunk_count||0} chunks{asset.duration_seconds?` · ${formatTime(asset.duration_seconds)}`:''}</small></div>{course.can_manage&&<button style={s.dangerIcon} onClick={()=>mutate(()=>removeLearningAsset(course.id,asset.id))} title="Remove from course"><Trash2 size={15}/></button>}</header>
    <div style={s.assetScope}><span>Current placement: <strong>{scopeLabel(course,mapping)}</strong></span></div>
    <div style={s.assetMappingControls}><MappingSelectors course={course} value={mapping} onChange={setMapping} disabled={!course.can_manage}/>{course.can_manage&&<button style={s.secondary} disabled={!changed} onClick={save}><Save size={14}/>Save mapping</button>}</div>
  </article>;
}

function MappingSelectors({course,value,onChange,disabled=false}) {
  const modules=course.modules||[];
  const selectedModule=modules.find(item=>String(item.id)===String(value.module_id));
  const lessons=selectedModule?.lessons||[];
  const setModule=module_id=>onChange({module_id,lesson_id:''});
  return <>
    <Field label="Module"><select disabled={disabled} value={value.module_id||''} onChange={e=>setModule(e.target.value)}><option value="">Entire course</option>{modules.map((module,index)=><option key={module.id} value={module.id}>Module {index+1}: {module.title}</option>)}</select></Field>
    <Field label="Lesson"><select disabled={disabled||!value.module_id} value={value.lesson_id||''} onChange={e=>onChange({...value,lesson_id:e.target.value})}><option value="">All lessons in module</option>{lessons.map((lesson,index)=><option key={lesson.id} value={lesson.id}>Lesson {index+1}: {lesson.title}</option>)}</select></Field>
  </>;
}

function ScopeBar({course,scope,setScope,count}) {
  return <div style={s.scopeBar}><div><strong>Learning scope</strong><small>{count} embedded {count===1?'asset':'assets'} available</small></div><div style={s.scopeSelectors}><MappingSelectors course={course} value={scope} onChange={setScope}/></div></div>;
}

function scopedDocumentIds(course,scope) {
  return [...new Set((course.assets||[]).filter(asset=>{
    if(asset.status!=='embedded')return false;
    if(!scope.module_id)return true;
    if(!asset.module_id)return true;
    if(String(asset.module_id)!==String(scope.module_id))return false;
    if(!scope.lesson_id)return true;
    return !asset.lesson_id||String(asset.lesson_id)===String(scope.lesson_id);
  }).map(asset=>asset.document_id))];
}

function scopeLabel(course,scope) {
  if(!scope.module_id)return 'Entire course';
  const module=(course.modules||[]).find(item=>String(item.id)===String(scope.module_id));
  if(!module)return 'Unassigned curriculum item';
  if(!scope.lesson_id)return module.title;
  const lesson=(module.lessons||[]).find(item=>String(item.id)===String(scope.lesson_id));
  return lesson?`${module.title} / ${lesson.title}`:module.title;
}

function studyScopeInstruction(course,scope) {
  if(!scope.module_id)return 'TARGET SCOPE: Entire course. Synthesize the selected course content across all modules.';
  const module=(course.modules||[]).find(item=>String(item.id)===String(scope.module_id));
  if(!module)return 'TARGET SCOPE: The selected curriculum scope only.';
  if(!scope.lesson_id){
    const lessonTitles=(module.lessons||[]).map(item=>item.title).filter(Boolean).join('; ');
    return `TARGET SCOPE: Module "${module.title}" only.${module.description?` Module description: ${module.description}`:''}${lessonTitles?` Included lessons: ${lessonTitles}.`:''} Cover this module as a whole and exclude material belonging only to other modules.`;
  }
  const lesson=(module.lessons||[]).find(item=>String(item.id)===String(scope.lesson_id));
  if(!lesson)return `TARGET SCOPE: Module "${module.title}" only. Exclude other modules.`;
  return `TARGET SCOPE: Lesson "${lesson.title}" within module "${module.title}" only.${lesson.description?` Lesson description: ${lesson.description}`:''} Summarize and generate material specifically for this lesson. Do not produce an entire-module or entire-course summary. Shared module or course assets are supporting context only; use only portions relevant to this lesson.`;
}

function Tutor({course,workspaceId}) {
  const [messages,setMessages]=useState([]); const [input,setInput]=useState(''); const [thinking,setThinking]=useState(false); const [sessionId,setSessionId]=useState('');
  const [scope,setScope]=useState({module_id:'',lesson_id:''});
  const [language,setLanguage]=useState('auto'); const [voiceMode,setVoiceMode]=useState('idle'); const [voiceStatus,setVoiceStatus]=useState('');
  const recognitionRef=useRef(null); const recorderRef=useRef(null); const streamRef=useRef(null); const audioChunksRef=useRef([]); const spokenRef=useRef('');
  useEffect(()=>setScope({module_id:'',lesson_id:''}),[course.id]);
  const documentIds=useMemo(()=>scopedDocumentIds(course,scope),[course.assets,scope.module_id,scope.lesson_id]);
  const sessionKey=`learning_session_${course.id}_${scope.module_id||'course'}_${scope.lesson_id||'all'}`;
  useEffect(()=>{let active=true;setMessages([]);setSessionId('');const id=localStorage.getItem(sessionKey);if(id)getSession(id).then(session=>{if(active){setSessionId(id);setMessages(session.messages||[])}}).catch(()=>localStorage.removeItem(sessionKey));return()=>{active=false}},[course.id,scope.module_id,scope.lesson_id]);
  useEffect(()=>()=>{
    recognitionRef.current?.abort?.();
    if(recorderRef.current?.state==='recording')recorderRef.current.stop();
    streamRef.current?.getTracks?.().forEach(track=>track.stop());
  },[]);
  const ask=async question=>{
    const content=String(question||'').trim();
    if(!content||thinking||!documentIds.length)return;
    setThinking(true);setVoiceStatus('');
    const user={role:'user',content};const assistant={role:'assistant',content:'',sources:null};const base=[...messages,user];
    setMessages([...base,assistant]);setInput('');let sid=sessionId;
    try{
      if(!sid){const created=await createSession(`${course.title} · ${scopeLabel(course,scope)} · Learning`,documentIds,workspaceId);sid=created.id;setSessionId(sid);localStorage.setItem(sessionKey,sid)}
      let answer='';
      await streamChat({question:user.content,documentIds,history:messages.slice(-10).map(({role,content:messageContent})=>({role,content:messageContent})),workspaceId,responseLanguage:language==='auto'?null:language.split('-')[0]},{
        onToken:t=>{answer+=t;setMessages([...base,{...assistant,content:answer}])},
        onDone:async sources=>{const done=[...base,{...assistant,content:answer,sources:sources||null}];setMessages(done);await saveSessionMessages(sid,done);setThinking(false)},
        onError:e=>{setMessages([...base,{...assistant,content:`Unable to answer: ${e}`}]);setThinking(false)},
      });
    }catch(e){setMessages([...base,{...assistant,content:`Unable to answer: ${e.message||e}`}]);setThinking(false)}
  };
  const finishRecordedAudio=async(recorder,stream)=>{
    stream.getTracks().forEach(track=>track.stop());streamRef.current=null;recorderRef.current=null;
    const blob=new Blob(audioChunksRef.current,{type:recorder.mimeType||'audio/webm'});audioChunksRef.current=[];
    if(!blob.size){setVoiceMode('idle');setVoiceStatus('No audio was captured.');return}
    setVoiceMode('transcribing');setVoiceStatus('Transcribing your question...');
    try{
      const result=await transcribeVoice(blob,language==='auto'?'':language);
      const transcript=String(result?.text||'').trim();
      if(!transcript){setVoiceStatus('No speech was detected.');return}
      setInput('');setVoiceStatus('Sending voice question...');await ask(transcript);
    }catch(e){setVoiceStatus(`Voice transcription failed: ${e.message||e}`)}finally{setVoiceMode('idle')}
  };
  const startRecorder=async()=>{
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){setVoiceStatus('Voice recording is not supported in this browser.');return}
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});const mimeType=supportedRecordingType();
      const recorder=new MediaRecorder(stream,mimeType?{mimeType}:undefined);audioChunksRef.current=[];streamRef.current=stream;recorderRef.current=recorder;
      recorder.ondataavailable=event=>{if(event.data?.size)audioChunksRef.current.push(event.data)};
      recorder.onstop=()=>finishRecordedAudio(recorder,stream);
      recorder.start();setVoiceMode('recording');setVoiceStatus('Recording. Select Stop when your question is complete.');
    }catch(e){setVoiceMode('idle');setVoiceStatus(e.name==='NotAllowedError'?'Microphone permission was not granted.':`Unable to start microphone: ${e.message||e}`)}
  };
  const toggleVoice=async()=>{
    if(voiceMode==='listening'){recognitionRef.current?.stop?.();return}
    if(voiceMode==='recording'){recorderRef.current?.stop?.();return}
    if(voiceMode==='transcribing'||thinking||!documentIds.length)return;
    const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(!SpeechRecognition){await startRecorder();return}
    const recognition=new SpeechRecognition();recognition.lang=language==='auto'?(navigator.language||'en-US'):language;recognition.continuous=false;recognition.interimResults=true;recognition.maxAlternatives=1;spokenRef.current='';
    recognition.onresult=event=>{let finalText=spokenRef.current;let interim='';for(let i=event.resultIndex;i<event.results.length;i+=1){const part=event.results[i][0]?.transcript||'';if(event.results[i].isFinal)finalText=`${finalText} ${part}`.trim();else interim+=part}spokenRef.current=finalText;setInput(`${finalText}${interim?` ${interim}`:''}`.trim())};
    recognition.onerror=event=>{recognitionRef.current=null;setVoiceMode('idle');setVoiceStatus(event.error==='not-allowed'?'Microphone permission was not granted.':`Voice recognition stopped: ${event.error}`)};
    recognition.onend=()=>{const spoken=spokenRef.current.trim();recognitionRef.current=null;setVoiceMode('idle');if(spoken){setInput('');setVoiceStatus('Sending voice question...');ask(spoken)}else setVoiceStatus(current=>current||'No speech was detected.')};
    recognitionRef.current=recognition;recognition.start();setVoiceMode('listening');setVoiceStatus(`Listening in ${tutorLanguageName(language)}. Pause when finished.`);
  };
  const voiceActive=voiceMode==='listening'||voiceMode==='recording';
  return <div style={s.tutor}><ScopeBar course={course} scope={scope} setScope={setScope} count={documentIds.length}/><div style={s.messages}>{!messages.length&&<Empty title="Ask the course" text={documentIds.length?'Questions use embedded content available to the selected curriculum scope.':'No embedded content is mapped to this curriculum scope.'}/>} {messages.map((message,index)=><article key={index} style={{...s.message,...(message.role==='user'?s.userMessage:s.aiMessage)}}><strong>{message.role==='user'?'You':'DocIntel Tutor'}</strong>{message.role==='user'?<div style={s.messageText}>{message.content}</div>:<MarkdownRenderer text={message.content||''} style={s.tutorAnswer}/>} {message.sources?.length>0&&<details style={s.sources}><summary>Sources ({message.sources.length})</summary>{message.sources.map((source,i)=><div key={i}>{source.original_name||source.filename||`Source ${i+1}`}{source.chunk_index!=null?` · chunk ${source.chunk_index+1}`:''}{source.start_seconds!=null?` · ${formatTime(source.start_seconds)}`:''}</div>)}</details>}</article>)}{thinking&&<div style={s.loading}><LoaderCircle size={14}/> Retrieving and reasoning...</div>}</div><form style={s.composer} onSubmit={e=>{e.preventDefault();ask(input)}}><select value={language} onChange={e=>setLanguage(e.target.value)} style={s.tutorLanguage} disabled={voiceActive||voiceMode==='transcribing'} aria-label="Tutor language" title="Question and response language">{TUTOR_LANGUAGES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><textarea value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();ask(input)}}} placeholder="Ask a grounded question about this course..." disabled={thinking}/><button type="button" style={{...s.voiceButton,...(voiceActive?s.voiceButtonOn:{})}} onClick={toggleVoice} disabled={thinking||voiceMode==='transcribing'||!documentIds.length} title={voiceActive?'Stop voice input':'Ask with voice'} aria-label={voiceActive?'Stop voice input':'Ask with voice'}>{voiceMode==='transcribing'?<LoaderCircle size={17}/>:voiceActive?<Square size={15}/>:<Mic size={18}/>}</button><button type="submit" style={s.send} disabled={thinking||!input.trim()||!documentIds.length} title="Send"><Send size={18}/></button>{voiceStatus&&<div style={s.voiceStatus} role="status">{voiceStatus}</div>}</form></div>;
}

function StudyTools({course,workspaceId,mutate}) {
  const [type,setType]=useState('study_guide'); const [generating,setGenerating]=useState(false); const [scope,setScope]=useState({module_id:'',lesson_id:''}); const [toolError,setToolError]=useState('');
  useEffect(()=>setScope({module_id:'',lesson_id:''}),[course.id]);
  const ids=useMemo(()=>scopedDocumentIds(course,scope),[course.assets,scope.module_id,scope.lesson_id]);
  const selectedScope=scopeLabel(course,scope);
  const generateContent=prompt=>new Promise((resolve,reject)=>{let content='';const scopedPrompt=`${studyScopeInstruction(course,scope)}\n\nTASK:\n${prompt}`;streamChat({question:scopedPrompt,documentIds:ids,history:[],workspaceId},{onToken:t=>content+=t,onDone:()=>resolve(content),onError:reject})});
  const generate=async()=>{
    if(!ids.length)return;
    setGenerating(true);setToolError('');
    try{
      let content=await generateContent(STUDY_PROMPTS[type]);
      if(type==='practice_questions'){
        try{content=JSON.stringify(parseGeneratedQuiz(content))}
        catch(firstError){
          console.warn('Retrying malformed practice quiz generation',firstError);
          content=JSON.stringify(parseGeneratedQuiz(await generateContent(PRACTICE_RETRY_PROMPT)));
        }
      }
      await mutate(()=>saveLearningArtifact(course.id,{artifact_type:type,title:`${pretty(type)} · ${selectedScope}`,content,source_document_ids:ids}));
    }catch(e){
      console.error(e);setToolError(e.message||String(e));
    }finally{setGenerating(false)}
  };
  return <div style={s.scroll}><ScopeBar course={course} scope={scope} setScope={setScope} count={ids.length}/><div style={s.studyTarget}><strong>Generation target</strong><span>{selectedScope}</span></div><div style={s.toolbar}><p>Generate source-grounded learning material from the selected curriculum scope.</p><select value={type} onChange={e=>setType(e.target.value)}>{Object.keys(STUDY_PROMPTS).map(x=><option key={x} value={x}>{pretty(x)}</option>)}</select><button style={s.primary} disabled={generating||!ids.length} onClick={generate}><Sparkles size={15}/>{generating?'Generating...':'Generate'}</button></div>
  {toolError&&<div style={s.error}>Practice questions could not be generated in the required interactive format. {toolError}</div>}
  <div style={s.artifactGrid}>{(course.artifacts||[]).map(item=>{const interactive=['practice_questions','flashcards'].includes(item.artifact_type);return <article style={{...s.artifact,...(interactive?s.quizArtifact:{})}} key={item.id}><header style={s.artifactHeader}><div><strong>{item.title||pretty(item.artifact_type)}</strong><small>{new Date(item.created_at).toLocaleString()}</small></div><button style={s.dangerIcon} onClick={()=>mutate(()=>deleteLearningArtifact(course.id,item.id))}><Trash2 size={14}/></button></header>{item.artifact_type==='practice_questions'?<PracticeQuiz content={item.content}/>:item.artifact_type==='flashcards'?<FlashcardDeck content={item.content}/>:<ExpandableMarkdown title={item.title||pretty(item.artifact_type)} text={item.content}/>}</article>})}</div>{!course.artifacts?.length&&<Empty title="No saved study materials" text="Generate a summary, study guide, key concepts, flashcards, or practice questions."/>}</div>;
}

function parseFlashcards(content) {
  const normalized=String(content||'')
    .replace(/\r/g,'')
    .replace(/^\s*(?:#{1,6}\s*)?(?:\*\*)?FLASHCARD\s+\d+(?:\*\*)?\s*$/gim,'')
    .trim();
  const cards=[];
  const pattern=/(?:^|\n)\s*(?:\d+[.)]\s*)?(?:\*\*)?(?:QUESTION|Q)(?:\s+\d+)?(?:\*\*)?\s*[:.-]\s*([\s\S]*?)\n+\s*(?:\*\*)?(?:ANSWER|A)(?:\s+\d+)?(?:\*\*)?\s*[:.-]\s*([\s\S]*?)(?=\n\s*(?:\d+[.)]\s*)?(?:\*\*)?(?:QUESTION|Q)(?:\s+\d+)?(?:\*\*)?\s*[:.-]|$)/gi;
  let match;
  while((match=pattern.exec(normalized))!==null){
    const question=match[1].trim(); const answer=match[2].trim();
    if(question&&answer)cards.push({question,answer});
  }
  return cards;
}

function FlashcardDeck({content}) {
  const cards=useMemo(()=>parseFlashcards(content),[content]);
  if(!cards.length)return <ExpandableText title="Flashcards" text={content}/>;
  return <div style={s.flashcardDeck}>{cards.map((card,index)=><section key={`${index}-${card.question}`} style={s.flashcardExchange}>
    <div style={s.flashcardLabel}>Flashcard {index+1}</div>
    <article style={{...s.message,...s.userMessage,...s.flashcardMessage}}><strong>You</strong><div style={s.messageText}>{card.question}</div></article>
    <article style={{...s.message,...s.aiMessage,...s.flashcardMessage}}><strong>DocIntel Tutor</strong><MarkdownRenderer text={card.answer} style={s.tutorAnswer}/></article>
  </section>)}</div>;
}

function parseGeneratedQuiz(raw) {
  const cleaned=String(raw||'').replace(/^\uFEFF/,'').replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'').trim();
  const start=cleaned.indexOf('{'); const end=cleaned.lastIndexOf('}');
  if(start<0||end<=start)throw new Error('The AI response ended before the quiz JSON was complete.');
  let quiz;
  const candidate=cleaned.slice(start,end+1).replace(/,\s*([}\]])/g,'$1');
  try{quiz=JSON.parse(candidate)}catch{throw new Error('The AI response contained invalid quiz JSON after an automatic retry.')}
  if(!Array.isArray(quiz.questions)||!quiz.questions.length)throw new Error('The generated quiz did not include questions.');
  const questions=quiz.questions.map((question,index)=>{
    if(!question?.question||!Array.isArray(question.options)||question.options.length!==4)throw new Error(`Question ${index+1} must contain a question and exactly four options.`);
    const options=question.options.map((option,optionIndex)=>({id:'ABCD'[optionIndex],text:String(option?.text||'').trim(),correct:option?.correct===true}));
    if(options.some(option=>!option.text)||!options.some(option=>option.correct))throw new Error(`Question ${index+1} must contain four option texts and at least one correct answer.`);
    if(new Set(options.map(option=>option.id)).size!==4)throw new Error(`Question ${index+1} must use four distinct option IDs.`);
    return {id:String(question.id||`q${index+1}`),question:String(question.question).trim(),options,explanation:String(question.explanation||'Review the correct answer against the cited course material.').trim()};
  });
  return {schema_version:1,instructions:String(quiz.instructions||'Select every correct answer.'),questions};
}

function storedQuiz(content) {
  try{return parseGeneratedQuiz(content)}catch{return null}
}

function PracticeQuiz({content}) {
  const mobile=useMobile();
  const quiz=useMemo(()=>storedQuiz(content),[content]);
  const [selections,setSelections]=useState({}); const [submitted,setSubmitted]=useState({});
  useEffect(()=>{setSelections({});setSubmitted({})},[content]);
  if(!quiz)return <ExpandableText title="Practice Questions" text={content}/>;
  const toggle=(questionId,optionId)=>setSelections(current=>{
    const selected=new Set(current[questionId]||[]);
    selected.has(optionId)?selected.delete(optionId):selected.add(optionId);
    return {...current,[questionId]:[...selected]};
  });
  const result=question=>{
    const chosen=[...(selections[question.id]||[])].sort();
    const correct=question.options.filter(option=>option.correct).map(option=>option.id).sort();
    return chosen.length===correct.length&&chosen.every((id,index)=>id===correct[index]);
  };
  const completed=quiz.questions.filter(question=>submitted[question.id]);
  const correctCount=completed.filter(result).length;
  const reset=()=>{setSelections({});setSubmitted({})};
  return <div style={s.quiz}>
    <div style={s.quizSummary}><div><strong>Interactive practice</strong><small>{quiz.instructions}</small></div><div style={s.quizScore}><span>{correctCount} correct</span><span>{completed.length} of {quiz.questions.length} submitted</span></div>{completed.length>0&&<button style={s.secondary} onClick={reset}><RotateCcw size={14}/>Try again</button>}</div>
    {quiz.questions.map((question,index)=>{const done=Boolean(submitted[question.id]);const correct=done&&result(question);const correctOptions=question.options.filter(option=>option.correct);return <section key={question.id} style={s.quizQuestion}>
      <header style={s.quizQuestionHeader}><span style={s.questionNumber}>{index+1}</span><strong>{question.question}</strong>{done&&(correct?<span style={s.correctBadge}><CheckCircle2 size={15}/>Correct</span>:<span style={s.incorrectBadge}><XCircle size={15}/>Incorrect</span>)}</header>
      <div style={{...s.quizOptions,...(mobile?{gridTemplateColumns:'1fr'}:{})}}>{question.options.map(option=>{const selected=(selections[question.id]||[]).includes(option.id);const answerStyle=done&&option.correct?s.correctOption:done&&selected&&!option.correct?s.incorrectOption:{};return <label key={option.id} style={{...s.quizOption,...answerStyle}}><input type="checkbox" checked={selected} disabled={done} onChange={()=>toggle(question.id,option.id)}/><b>{option.id}</b><span>{option.text}</span>{done&&option.correct&&<CheckCircle2 size={16}/>}</label>})}</div>
      {!done?<button style={s.primary} disabled={!(selections[question.id]||[]).length} onClick={()=>setSubmitted(current=>({...current,[question.id]:true}))}>Submit answer</button>:<div style={{...s.answerFeedback,...(correct?s.correctFeedback:s.incorrectFeedback)}}><strong>{correct?'Correct answer':'Correct answer shown below'}</strong><span>{correctOptions.map(option=>`${option.id}. ${option.text}`).join(' | ')}</span><p>{question.explanation}</p></div>}
    </section>})}
  </div>;
}

function Questions({course,mutate}) {
  const [form,setForm]=useState({target_role:'teacher',question:''});
  const submit=async()=>{await mutate(()=>createLearningQuestion(course.id,{...form,context:{course_title:course.title,source_document_ids:(course.assets||[]).map(x=>x.document_id)}}));setForm({...form,question:''})};
  return <div style={s.scroll}><Section title="Ask a person" icon={<CircleHelp size={16}/>}><div style={s.questionForm}><select value={form.target_role} onChange={e=>setForm({...form,target_role:e.target.value})}><option value="teacher">Teacher</option><option value="advisor">Advisor</option></select><textarea placeholder="What needs human guidance or clarification?" value={form.question} onChange={e=>setForm({...form,question:e.target.value})}/><button style={s.primary} disabled={!form.question.trim()} onClick={submit}><Send size={15}/>Send</button></div></Section>
  <div style={s.cards}>{(course.questions||[]).map(item=><QuestionCard key={item.id} item={item} course={course} mutate={mutate}/>)}</div>{!course.questions?.length&&<Empty title="No escalations" text="Questions that require judgment can be routed to a teacher or advisor with course context."/>}</div>;
}

function QuestionCard({item,course,mutate}) { const [answer,setAnswer]=useState(item.answer||'');const canAnswer=['teacher','advisor','admin'].includes(course.my_persona)||course.can_manage;return <article style={s.question}><header><span style={s.status}>{item.status}</span><small>To {item.target_role} · {item.asker_name||item.asker_email}</small></header><strong>{item.question}</strong>{(canAnswer||item.answer)&&<textarea disabled={!canAnswer||item.status==='closed'} value={answer} placeholder="Write a reviewed response..." onChange={e=>setAnswer(e.target.value)}/>}<div style={s.inlineActions}>{canAnswer&&item.status!=='closed'&&<button style={s.primary} disabled={!answer.trim()} onClick={()=>mutate(()=>updateLearningQuestion(course.id,item.id,{answer,status:'answered'}))}><Save size={14}/>Answer</button>}{item.status==='answered'&&<button style={s.secondary} onClick={()=>mutate(()=>updateLearningQuestion(course.id,item.id,{status:'closed'}))}>Close</button>}</div></article>}

function Section({title,icon,children}) { return <section style={s.section}><h3>{icon}{title}</h3>{children}</section> }
function Field({label,wide,children}) { return <div style={{...s.field,...(wide?s.wide:{})}}><span>{label}</span>{children}</div> }
function Empty({title,text}) { return <div style={s.empty}><BookOpen size={23}/><strong>{title}</strong><span>{text}</span></div> }
function ExpandableEditor({title,value,onChange,placeholder='',disabled=false,compact=false}) {
  const [open,setOpen]=useState(false);
  useEffect(()=>{
    if(!open)return undefined;
    const close=e=>e.key==='Escape'&&setOpen(false);
    window.addEventListener('keydown',close);
    return()=>window.removeEventListener('keydown',close);
  },[open]);
  const expand=()=>setOpen(true);
  return <div style={s.editorField}>
    <textarea
      style={{...s.scrollableTextarea,...(compact?s.compactTextarea:{})}}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={e=>onChange(e.target.value)}
      onClick={expand}
      aria-label={title}
    />
    <button type="button" style={s.expandBtn} onClick={expand} title={`Expand ${title}`} aria-label={`Expand ${title}`}><Maximize2 size={14}/></button>
    {open&&<div style={s.readerOverlay} role="dialog" aria-modal="true" aria-label={title} onMouseDown={e=>e.target===e.currentTarget&&setOpen(false)}>
      <div style={s.editorDialog}>
        <header style={s.editorHeader}><strong>{title}</strong><button type="button" style={s.iconBtn} onClick={()=>setOpen(false)} title="Close editor" aria-label="Close editor"><X size={18}/></button></header>
        <textarea autoFocus={!disabled} readOnly={disabled} style={s.largeEditor} value={value} placeholder={placeholder} onChange={e=>!disabled&&onChange(e.target.value)}/>
        <footer style={s.editorFooter}><span>{disabled?'Read-only course content.':'Changes are kept in this form. Use Save to persist them.'}</span><button type="button" style={s.primary} onClick={()=>setOpen(false)}>Done</button></footer>
      </div>
    </div>}
  </div>;
}
function ExpandableText({title,text}) { const [open,setOpen]=useState(false);return <><div style={s.clamped}>{text}</div><button style={s.linkBtn} onClick={()=>setOpen(true)}>Read full content</button>{open&&<div style={s.readerOverlay} onMouseDown={e=>e.target===e.currentTarget&&setOpen(false)}><div style={s.reader}><header style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:10,borderBottom:'1px solid var(--b1)'}}><strong>{title}</strong><button style={s.iconBtn} onClick={()=>setOpen(false)}><X size={18}/></button></header><pre style={{flex:1,minHeight:0,overflow:'auto',padding:14,whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontFamily:'inherit',lineHeight:1.55,color:'var(--tx2)'}}>{text}</pre></div></div>}</> }
function ExpandableMarkdown({title,text}) {
  const [open,setOpen]=useState(false);
  return <><div style={s.markdownPreview}><MarkdownRenderer text={text||''} style={s.artifactMarkdown}/></div><button type="button" style={s.linkBtn} onClick={()=>setOpen(true)}>Open formatted view</button>{open&&<div style={s.readerOverlay} role="dialog" aria-modal="true" aria-label={title} onMouseDown={e=>e.target===e.currentTarget&&setOpen(false)}><div style={s.reader}><header style={s.readerHeader}><strong>{title}</strong><button type="button" style={s.iconBtn} onClick={()=>setOpen(false)} title="Close" aria-label="Close"><X size={18}/></button></header><div style={s.markdownReader}><MarkdownRenderer text={text||''} style={s.readerMarkdown}/></div></div></div>}</>;
}
function useMobile(){const[m,setM]=useState(()=>window.innerWidth<760);useEffect(()=>{const fn=()=>setM(window.innerWidth<760);window.addEventListener('resize',fn);return()=>window.removeEventListener('resize',fn)},[]);return m}
const pretty=value=>String(value||'').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const formatTime=value=>{const total=Math.max(0,Number(value)||0);return `${Math.floor(total/60)}:${String(Math.floor(total%60)).padStart(2,'0')}`};

const s={
  overlay:{position:'fixed',inset:0,zIndex:2100,display:'flex',alignItems:'center',justifyContent:'center',padding:10,background:'rgba(0,0,0,.7)'},
  panel:{width:'min(1240px,100%)',height:'min(94dvh,960px)',display:'flex',flexDirection:'column',overflow:'hidden',border:'1px solid rgba(74,222,128,.22)',borderRadius:10,background:'#0f1f0f',color:'var(--tx)',boxShadow:'0 28px 80px rgba(0,0,0,.6)'},panelMobile:{width:'100%',height:'100dvh',borderRadius:0,padding:0},
  header:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,padding:'10px 13px',borderBottom:'1px solid var(--b1)',background:'#142714',flexShrink:0},title:{display:'flex',alignItems:'center',gap:7,margin:0,fontSize:17},subtitle:{marginTop:2,fontSize:10.5,color:'var(--muted2)'},headerActions:{display:'flex',gap:6},iconBtn:{display:'grid',placeItems:'center',width:34,height:34,padding:0,border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'var(--tx)',cursor:'pointer'},
  courseBar:{display:'flex',alignItems:'end',gap:8,padding:'8px 12px',borderBottom:'1px solid var(--b1)',background:'var(--s1)',overflowX:'auto',flexShrink:0},inlineField:{display:'flex',flexDirection:'column',gap:3,minWidth:190,fontSize:10,color:'var(--muted2)'},persona:{alignSelf:'center',padding:'4px 8px',border:'1px solid rgba(74,222,128,.3)',borderRadius:20,color:'#86efac',fontSize:10,textTransform:'capitalize'},
  loading:{display:'flex',alignItems:'center',gap:6,padding:'6px 12px',fontSize:11,color:'#86efac',background:'rgba(74,222,128,.06)'},error:{padding:'7px 12px',fontSize:11,color:'#fecaca',background:'rgba(248,113,113,.1)'},
  tabs:{display:'flex',gap:3,padding:'0 10px',borderBottom:'1px solid var(--b1)',overflowX:'auto',flexShrink:0},tab:{padding:'9px 10px',border:0,borderBottom:'2px solid transparent',background:'transparent',color:'var(--muted2)',whiteSpace:'nowrap',cursor:'pointer'},tabOn:{color:'#4ade80',borderBottomColor:'#4ade80'},mobileTabs:{margin:8,width:'calc(100% - 16px)',padding:9,border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'var(--tx)'},
  body:{flex:1,minHeight:0,overflow:'hidden'},scroll:{height:'100%',overflowY:'auto',padding:'10px 12px 28px',boxSizing:'border-box'},section:{marginBottom:12,padding:12,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},grid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(190px,1fr))',gap:9,marginBottom:10},field:{display:'flex',flexDirection:'column',gap:4,minWidth:0,fontSize:11,color:'var(--muted2)'},wide:{gridColumn:'1/-1'},
  primary:{display:'inline-flex',alignItems:'center',justifyContent:'center',gap:6,minHeight:34,padding:'7px 11px',border:0,borderRadius:6,background:'#15803d',color:'#fff',fontWeight:800,cursor:'pointer',whiteSpace:'nowrap'},secondary:{display:'inline-flex',alignItems:'center',justifyContent:'center',gap:5,minHeight:34,padding:'7px 10px',border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'var(--tx)',cursor:'pointer',whiteSpace:'nowrap'},danger:{display:'inline-flex',alignItems:'center',gap:6,padding:8,border:'1px solid rgba(248,113,113,.3)',borderRadius:6,background:'rgba(248,113,113,.08)',color:'#fca5a5',cursor:'pointer'},dangerIcon:{display:'grid',placeItems:'center',width:30,height:30,padding:0,border:'1px solid rgba(248,113,113,.22)',borderRadius:5,background:'rgba(248,113,113,.07)',color:'#fca5a5',cursor:'pointer',flexShrink:0},
  addRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,190px),1fr))',gap:7,alignItems:'end'},cards:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,280px),1fr))',gap:8},card:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:9,border:'1px solid var(--b1)',borderRadius:6,background:'var(--s2)'},lesson:{display:'grid',gridTemplateColumns:'30px minmax(0,1fr) 30px',gap:8,alignItems:'center',marginBottom:9,padding:'8px 0',borderBottom:'1px solid var(--b1)'},lessonNumber:{alignSelf:'start',display:'grid',placeItems:'center',width:25,height:25,borderRadius:5,background:'rgba(74,222,128,.1)',color:'#86efac',fontSize:11,fontWeight:800},lessonFields:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,220px),1fr))',gap:8,minWidth:0},inlineActions:{display:'flex',justifyContent:'flex-end',gap:6,marginTop:7},toolbar:{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap',marginBottom:10},mappingRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,190px),1fr))',gap:8,alignItems:'end'},
  assetGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,430px),1fr))',gap:8},asset:{display:'grid',gridTemplateColumns:'38px minmax(0,1fr) 30px',gap:9,alignItems:'center',padding:10,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},assetIcon:{display:'grid',placeItems:'center',width:36,height:36,borderRadius:6,background:'rgba(74,222,128,.1)',color:'#4ade80'},assetMapping:{minWidth:0,padding:10,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},assetHeader:{display:'flex',alignItems:'center',gap:9,minWidth:0},assetScope:{margin:'8px 0',padding:'6px 8px',borderRadius:5,background:'rgba(74,222,128,.06)',color:'var(--muted2)',fontSize:10.5},assetMappingControls:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,170px),1fr))',gap:7,alignItems:'end'},ellipsis:{display:'block',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'},scopeBar:{display:'flex',alignItems:'end',justifyContent:'space-between',gap:10,padding:'8px 12px',borderBottom:'1px solid var(--b1)',background:'var(--s1)',flexWrap:'wrap'},scopeSelectors:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,180px),1fr))',gap:7,flex:'1 1 390px',maxWidth:520},studyTarget:{display:'flex',alignItems:'center',gap:7,margin:'8px 0',padding:'7px 9px',border:'1px solid rgba(74,222,128,.2)',borderRadius:6,background:'rgba(74,222,128,.06)',color:'var(--muted2)',fontSize:11},
  tutor:{height:'100%',display:'flex',flexDirection:'column'},messages:{flex:1,minHeight:0,overflowY:'auto',padding:12},message:{maxWidth:'min(820px,92%)',marginBottom:9,padding:'9px 11px',borderRadius:7},userMessage:{marginLeft:'auto',background:'#166534',color:'#fff'},aiMessage:{marginRight:'auto',border:'1px solid var(--b1)',background:'var(--s1)'},messageText:{marginTop:5,whiteSpace:'pre-wrap',lineHeight:1.55,fontSize:13},tutorAnswer:{marginTop:5,fontSize:13,lineHeight:1.65,color:'var(--tx2)',overflowWrap:'anywhere'},sources:{marginTop:8,paddingTop:6,borderTop:'1px solid var(--b1)',fontSize:10.5,color:'var(--muted2)'},composer:{display:'grid',gridTemplateColumns:'minmax(78px,108px) minmax(0,1fr) 40px 40px',gap:7,padding:10,borderTop:'1px solid var(--b1)',background:'var(--s1)'},tutorLanguage:{minWidth:0,width:'100%',padding:'0 7px',border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'var(--tx)',fontSize:11},voiceButton:{display:'grid',placeItems:'center',padding:0,border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'#86efac',cursor:'pointer'},voiceButtonOn:{borderColor:'rgba(248,113,113,.55)',background:'rgba(248,113,113,.12)',color:'#fca5a5'},voiceStatus:{gridColumn:'1/-1',fontSize:10.5,color:'var(--muted2)'},send:{display:'grid',placeItems:'center',border:0,borderRadius:6,background:'#15803d',color:'#fff',cursor:'pointer'},
  artifactGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,360px),1fr))',gap:9},artifact:{padding:11,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},artifactHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8},quizArtifact:{gridColumn:'1/-1'},flashcardDeck:{display:'flex',flexDirection:'column',gap:12,marginTop:10},flashcardExchange:{display:'flex',flexDirection:'column',gap:7,padding:'10px 0',borderTop:'1px solid var(--b1)'},flashcardLabel:{alignSelf:'center',padding:'3px 8px',borderRadius:12,background:'rgba(74,222,128,.08)',color:'var(--muted2)',fontSize:10},flashcardMessage:{width:'min(820px,92%)',boxSizing:'border-box',marginBottom:0},quiz:{display:'flex',flexDirection:'column',gap:12,marginTop:10},quizSummary:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,flexWrap:'wrap',padding:10,border:'1px solid rgba(74,222,128,.22)',borderRadius:6,background:'rgba(74,222,128,.06)'},quizScore:{display:'flex',gap:6,flexWrap:'wrap',color:'#bbf7d0',fontSize:11},quizQuestion:{padding:12,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s2)'},quizQuestionHeader:{display:'flex',alignItems:'flex-start',gap:8,marginBottom:10},questionNumber:{display:'grid',placeItems:'center',width:25,height:25,flexShrink:0,borderRadius:5,background:'rgba(74,222,128,.12)',color:'#86efac',fontWeight:800},correctBadge:{display:'inline-flex',alignItems:'center',gap:4,marginLeft:'auto',padding:'3px 7px',borderRadius:12,background:'rgba(74,222,128,.12)',color:'#86efac',fontSize:10},incorrectBadge:{display:'inline-flex',alignItems:'center',gap:4,marginLeft:'auto',padding:'3px 7px',borderRadius:12,background:'rgba(248,113,113,.12)',color:'#fca5a5',fontSize:10},quizOptions:{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:7,marginBottom:10},quizOption:{display:'grid',gridTemplateColumns:'20px 24px minmax(0,1fr) 18px',alignItems:'center',gap:7,minHeight:48,padding:'8px 10px',border:'1px solid var(--b1)',borderRadius:6,background:'var(--s1)',color:'var(--tx2)',cursor:'pointer'},correctOption:{borderColor:'rgba(74,222,128,.55)',background:'rgba(74,222,128,.1)',color:'#dcfce7'},incorrectOption:{borderColor:'rgba(248,113,113,.55)',background:'rgba(248,113,113,.1)',color:'#fee2e2'},answerFeedback:{display:'flex',flexDirection:'column',gap:5,padding:10,borderRadius:6,lineHeight:1.45},correctFeedback:{border:'1px solid rgba(74,222,128,.35)',background:'rgba(74,222,128,.08)'},incorrectFeedback:{border:'1px solid rgba(248,113,113,.35)',background:'rgba(248,113,113,.08)'},clamped:{display:'-webkit-box',WebkitLineClamp:8,WebkitBoxOrient:'vertical',overflow:'hidden',marginTop:8,whiteSpace:'pre-wrap',fontSize:12,lineHeight:1.5,color:'var(--tx2)'},linkBtn:{marginTop:7,padding:0,border:0,background:'transparent',color:'#4ade80',cursor:'pointer'},questionForm:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,220px),1fr))',gap:7,alignItems:'end'},question:{padding:11,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},status:{padding:'3px 7px',borderRadius:12,background:'rgba(74,222,128,.1)',color:'#86efac',fontSize:10,textTransform:'uppercase'},
  empty:{height:'100%',minHeight:150,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:7,padding:18,textAlign:'center',color:'var(--muted2)'},markdownPreview:{maxHeight:360,marginTop:8,overflow:'auto',padding:'2px 8px 2px 2px',borderTop:'1px solid var(--b1)'},artifactMarkdown:{fontSize:13,lineHeight:1.65,color:'var(--tx2)'},readerOverlay:{position:'fixed',inset:0,zIndex:3300,display:'grid',placeItems:'center',padding:'max(12px,env(safe-area-inset-top)) max(12px,env(safe-area-inset-right)) max(12px,env(safe-area-inset-bottom)) max(12px,env(safe-area-inset-left))',background:'rgba(0,0,0,.8)'},reader:{width:'min(900px,100%)',height:'min(86dvh,760px)',display:'flex',flexDirection:'column',overflow:'hidden',border:'1px solid var(--b2)',borderRadius:8,background:'#102010'},readerHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:10,borderBottom:'1px solid var(--b1)',background:'#142714',flexShrink:0},markdownReader:{flex:1,minHeight:0,overflow:'auto',padding:16},readerMarkdown:{fontSize:14,lineHeight:1.75,color:'var(--tx)'},
  editorField:{position:'relative',minWidth:0,width:'100%'},scrollableTextarea:{display:'block',width:'100%',height:86,minHeight:64,maxHeight:180,overflowY:'auto',resize:'vertical',padding:'9px 36px 9px 10px',boxSizing:'border-box',lineHeight:1.45},compactTextarea:{height:58,minHeight:48,maxHeight:130},expandBtn:{position:'absolute',top:6,right:6,display:'grid',placeItems:'center',width:27,height:27,padding:0,border:'1px solid var(--b2)',borderRadius:5,background:'var(--s2)',color:'#86efac',cursor:'pointer'},editorDialog:{width:'min(980px,100%)',height:'min(88dvh,820px)',display:'flex',flexDirection:'column',overflow:'hidden',border:'1px solid rgba(74,222,128,.3)',borderRadius:8,background:'#102010',boxShadow:'0 24px 80px rgba(0,0,0,.65)'},editorHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:'10px 12px',borderBottom:'1px solid var(--b1)',background:'#142714',flexShrink:0},largeEditor:{flex:1,minHeight:0,width:'100%',overflow:'auto',resize:'none',padding:16,boxSizing:'border-box',border:0,borderRadius:0,background:'var(--s1)',color:'var(--tx)',fontFamily:'inherit',fontSize:15,lineHeight:1.6},editorFooter:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,padding:'9px 12px',borderTop:'1px solid var(--b1)',background:'#142714',color:'var(--muted2)',fontSize:10.5,flexShrink:0},
};
