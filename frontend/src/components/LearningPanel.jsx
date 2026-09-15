import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, BarChart3, BookOpen, CheckCircle2, ChevronDown, CircleHelp, ClipboardList, ExternalLink,
  FileText, GraduationCap, LoaderCircle, Maximize2, MessageSquareText, Mic, Plus, RefreshCw, RotateCcw,
  Save, Send, Sparkles, Square, Trash2, Upload, UserPlus, X, XCircle,
} from 'lucide-react';
import {
  addLearningAsset, addLearningMember, createLearningCourse, createLearningQuestion,
  createLearningAssignment, createSession, deleteLearningArtifact, deleteLearningAssignment, deleteLearningCourse,
  evaluateLearningSubmission, getLearningCourse, getLearningEvidenceUrl, getLearningInstructorDashboard,
  getLearningMastery, getSession,
  listLearningCourses, listLearningDocuments, removeLearningAsset, removeLearningMember,
  reviewLearningSubmission,
  resolveLearningScope, saveLearningArtifact, saveLearningCurriculum, saveLearningQuizAttempt,
  saveLearningSubmission, saveSessionMessages, streamChat, transcribeVoice, uploadDocuments,
  updateLearningAsset, updateLearningCourse, updateLearningLessonProgress, updateLearningQuestion,
} from '../services/api.js';
import MarkdownRenderer from './MarkdownRenderer.jsx';

const TABS = [
  ['overview', 'Overview'], ['curriculum', 'Curriculum'], ['content', 'Course Content'],
  ['tutor', 'AI Tutor'], ['study', 'Study Tools'], ['assignments', 'Assignments & Projects'],
  ['progress', 'Progress & Mastery'], ['instructor', 'Instructor Intelligence'], ['questions', 'Teacher / Advisor'],
];
const TUTOR_LANGUAGES = [
  ['auto', 'Auto'], ['en-US', 'English'], ['bn-BD', 'Bangla'],
  ['hi-IN', 'Hindi'], ['es-ES', 'Spanish'], ['ar-SA', 'Arabic'],
];
const DOMAIN_PACKS = [
  ['general','General learning'], ['healthcare','Healthcare education'],
  ['financial_services','Financial services'], ['manufacturing','Manufacturing and safety'],
  ['construction','Construction and field service'], ['sports','Sports and coaching'],
  ['legal_compliance','Legal and compliance'], ['enterprise_training','Enterprise training'],
  ['customer_education','Customer education'], ['government','Government and public sector'],
  ['cultural_arts','Cultural arts'],
];
const LEARNING_SESSION_SCOPE_VERSION = 'v3';
const TIMED_MEDIA_EXTENSION = /\.(mp4|mov|m4v|avi|mkv|webm|mp3|m4a|wav|ogg|aac|flac)$/i;
const tutorLanguageName=code=>TUTOR_LANGUAGES.find(([value])=>value===code)?.[1]||'selected language';
const isTimedLearningContent=item=>{
  if(!item)return false;
  const type=`${item.file_type||''} ${item.doc_type||''} ${item.content_type||''}`.toLowerCase();
  const name=item.original_name||item.filename||item.title||'';
  return /(^|[\s/])(video|audio)([\s/]|$)/.test(type)||TIMED_MEDIA_EXTENSION.test(name);
};
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
  const [selectedCourseId, setSelectedCourseId] = useState('');
  const [documents, setDocuments] = useState([]);
  const [tab, setTab] = useState('overview');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const courseRequestRef = useRef(0);

  const load = async (preferredId = '') => {
    if (!workspaceId) return;
    const requestId = ++courseRequestRef.current;
    setBusy(true); setError('');
    try {
      const [courseRows, docRows] = await Promise.all([listLearningCourses(workspaceId), listLearningDocuments(workspaceId)]);
      if(requestId!==courseRequestRef.current)return;
      setCourses(courseRows || []); setDocuments(docRows || []);
      const id = preferredId === null ? courseRows?.[0]?.id : (preferredId || course?.id || courseRows?.[0]?.id);
      setSelectedCourseId(id||'');
      const loadedCourse=id ? await getLearningCourse(id) : null;
      if(requestId===courseRequestRef.current)setCourse(loadedCourse);
    } catch (e) { if(requestId===courseRequestRef.current)setError(e.message || String(e)); }
    finally { if(requestId===courseRequestRef.current)setBusy(false); }
  };
  useEffect(() => { courseRequestRef.current+=1;setCourse(null);setSelectedCourseId('');setCourses([]);load(null); }, [workspaceId]);

  const selectCourse = async id => {
    const requestId=++courseRequestRef.current;
    setSelectedCourseId(id);setCourse(null);
    if(!id){setBusy(false);return}
    setBusy(true); setError('');
    try { const loadedCourse=await getLearningCourse(id);if(requestId===courseRequestRef.current)setCourse(loadedCourse); }
    catch (e) { setError(e.message || String(e)); }
    finally { if(requestId===courseRequestRef.current)setBusy(false); }
  };
  const mutate = async operation => {
    const requestId=courseRequestRef.current;
    const targetCourseId=course?.id;
    setBusy(true); setError('');
    try { const result = await operation(); if (targetCourseId&&requestId===courseRequestRef.current) setCourse(await getLearningCourse(targetCourseId)); return result; }
    catch (e) { setError(e.message || String(e)); throw e; }
    finally { if(requestId===courseRequestRef.current)setBusy(false); }
  };

  return <div style={s.overlay} role="dialog" aria-modal="true" aria-label="Knowledge Academy">
    <section style={{...s.panel, ...(mobile ? s.panelMobile : {})}}>
      <header style={s.header}>
        <div style={{minWidth:0}}><h2 style={s.title}><GraduationCap size={19}/> Knowledge Academy</h2><div style={s.subtitle}>Grounded learning across documents, recordings, and video</div></div>
        <div style={s.headerActions}>
          <button style={s.iconBtn} onClick={()=>load(course?.id)} title="Refresh"><RefreshCw size={16}/></button>
          <button style={s.iconBtn} onClick={onClose} title="Close"><X size={18}/></button>
        </div>
      </header>
      {!workspaceId ? <Empty title="Select a workspace" text="Learning courses are governed by a DocIntel workspace. Select or create one first."/> : <>
        <div style={s.courseBar}>
          <label style={s.inlineField}><span>Course</span><select value={selectedCourseId} onChange={e=>selectCourse(e.target.value)}><option value="">Create or select a course</option>{courses.map(item=><option key={item.id} value={item.id}>{item.course_code ? `${item.course_code} · ` : ''}{item.title}</option>)}</select></label>
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
            {tab==='tutor' && <Tutor key={`tutor-${course.id}`} course={course} workspaceId={workspaceId}/>}
            {tab==='study' && <StudyTools key={`study-${course.id}`} course={course} workspaceId={workspaceId} mutate={mutate}/>}
            {tab==='assignments' && <Assignments course={course} documents={documents} workspaceId={workspaceId} mutate={mutate}/>}
            {tab==='progress' && <LearningProgress key={`progress-${course.id}`} course={course}/>}
            {tab==='instructor' && <InstructorDashboard key={`instructor-${course.id}`} course={course}/>}
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
  const config=course.domain_config||{};
  const save=()=>mutate(()=>updateLearningCourse(course.id,{title:course.title,course_code:course.course_code||'',semester:course.semester||'',description:course.description||'',instructor_name:course.instructor_name||'',objectives:String((course.objectives||[]).join('\n')).split('\n').filter(Boolean),domain:course.domain||'general',publication_status:course.publication_status||'draft',domain_config:config}));
  return <div style={s.scroll}><Section title="Course profile" icon={<BookOpen size={16}/>}><div style={s.grid}>
    <Field label="Course title"><input value={course.title||''} onChange={e=>setCourse({...course,title:e.target.value})}/></Field>
    <Field label="Course code"><input value={course.course_code||''} onChange={e=>setCourse({...course,course_code:e.target.value})}/></Field>
    <Field label="Semester"><input value={course.semester||''} onChange={e=>setCourse({...course,semester:e.target.value})}/></Field>
    <Field label="Instructor"><input value={course.instructor_name||''} onChange={e=>setCourse({...course,instructor_name:e.target.value})}/></Field>
    <Field label="Domain pack"><select value={course.domain||'general'} onChange={e=>setCourse({...course,domain:e.target.value})}>{DOMAIN_PACKS.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label="Publication"><select value={course.publication_status||'draft'} onChange={e=>setCourse({...course,publication_status:e.target.value})}><option value="draft">Draft</option><option value="published">Published</option></select></Field>
    <Field label="Passing score (%)"><input type="number" min="0" max="100" value={config.passing_score??80} onChange={e=>setCourse({...course,domain_config:{...config,passing_score:Number(e.target.value)}})}/></Field>
    <Field label="Human reviewer"><select value={config.reviewer_persona||course.domain_pack?.reviewer_persona||'teacher'} onChange={e=>setCourse({...course,domain_config:{...config,reviewer_persona:e.target.value}})}><option value="teacher">Teacher</option><option value="advisor">Advisor</option></select></Field>
    <Field label="Description" wide><ExpandableEditor title="Course description" value={course.description||''} onChange={value=>setCourse({...course,description:value})}/></Field>
    <Field label="Learning objectives (one per line)" wide><ExpandableEditor title="Learning objectives" value={(course.objectives||[]).join('\n')} onChange={value=>setCourse({...course,objectives:value.split('\n')})}/></Field>
    <Field label="Domain Tutor guidance" wide><ExpandableEditor title="Domain Tutor guidance" value={config.tutor_focus||course.domain_pack?.tutor_focus||''} onChange={value=>setCourse({...course,domain_config:{...config,tutor_focus:value}})}/></Field>
    <Field label="Learning controls" wide><div style={s.checkRow}><label><input type="checkbox" checked={Boolean(config.certificate_enabled)} onChange={e=>setCourse({...course,domain_config:{...config,certificate_enabled:e.target.checked}})}/> Certificate enabled</label><label><input type="checkbox" checked={Boolean(config.acknowledgement_required)} onChange={e=>setCourse({...course,domain_config:{...config,acknowledgement_required:e.target.checked}})}/> Acknowledgment required</label></div></Field>
  </div>{course.can_manage&&<button style={s.primary} onClick={save}><Save size={15}/>Save course</button>}</Section>
  <Section title="Course members" icon={<UserPlus size={16}/>}><div style={s.addRow}><input placeholder="Member email" value={member.email} onChange={e=>setMember({...member,email:e.target.value})}/><select value={member.persona} onChange={e=>setMember({...member,persona:e.target.value})}>{['student','teacher','advisor','admin'].map(x=><option key={x}>{x}</option>)}</select><button style={s.secondary} disabled={!course.can_manage||!member.email} onClick={async()=>{await mutate(()=>addLearningMember(course.id,member));setMember({...member,email:''})}}><Plus size={15}/>Enroll</button></div>
  <div style={s.cards}>{(course.members||[]).map(item=><article style={s.card} key={item.user_id}><div style={s.metaStack}><strong>{item.full_name||item.email}</strong><small>{item.email} · {item.persona}</small></div>{course.can_manage&&item.user_id!==course.created_by&&<button style={s.dangerIcon} onClick={()=>mutate(()=>removeLearningMember(course.id,item.user_id))} title="Remove"><Trash2 size={15}/></button>}</article>)}</div></Section>
  {course.can_manage&&<button style={s.danger} onClick={()=>confirm('Delete this course and its learning records? Source documents will remain in DocIntel.')&&onDeleted()}><Trash2 size={15}/>Delete course</button>}</div>;
}

function Curriculum({course,mutate}) {
  const [modules,setModules]=useState(course.modules||[]); useEffect(()=>setModules(course.modules||[]),[course.id,course.modules]);
  const update=(mi,key,value)=>setModules(rows=>rows.map((row,i)=>i===mi?{...row,[key]:value}:row));
  const lesson=(mi,li,key,value)=>setModules(rows=>rows.map((row,i)=>i===mi?{...row,lessons:(row.lessons||[]).map((x,j)=>j===li?{...x,[key]:value}:x)}:row));
  return <div style={s.scroll}><div style={s.toolbar}><p>Organize a semester into modules and lessons. Course content remains reusable across the curriculum.</p>{course.can_manage&&<><button style={s.secondary} onClick={()=>setModules([...modules,{title:'',description:'',lessons:[]}])}><Plus size={15}/>Module</button><button style={s.primary} onClick={()=>mutate(()=>saveLearningCurriculum(course.id,modules))}><Save size={15}/>Save</button></>}</div>
  {modules.map((module,mi)=><Section key={mi} title={`Module ${mi+1}`}><div style={s.grid}><Field label="Title"><ExpandableEditor compact title={`Module ${mi+1} title`} placeholder="Module title" value={module.title||''} disabled={!course.can_manage} onChange={value=>update(mi,'title',value)}/></Field><Field label="Description" wide><ExpandableEditor title={`Module ${mi+1} description`} value={module.description||''} disabled={!course.can_manage} onChange={value=>update(mi,'description',value)}/></Field></div>
  {(module.lessons||[]).map((item,li)=><div style={s.lesson} key={li}><span style={s.lessonNumber}>{li+1}</span><div style={s.lessonFields}><Field label="Lesson title"><ExpandableEditor compact title={`Module ${mi+1}, lesson ${li+1} title`} placeholder="Lesson title" value={item.title||''} disabled={!course.can_manage} onChange={value=>lesson(mi,li,'title',value)}/></Field><Field label="Lesson description"><ExpandableEditor compact title={`Module ${mi+1}, lesson ${li+1} description`} placeholder="Lesson description" value={item.description||''} disabled={!course.can_manage} onChange={value=>lesson(mi,li,'description',value)}/></Field><Field label="Objectives"><ExpandableEditor compact title={`Module ${mi+1}, lesson ${li+1} objectives`} placeholder="One objective per line" value={(item.objectives||[]).join('\n')} disabled={!course.can_manage} onChange={value=>lesson(mi,li,'objectives',value.split('\n'))}/></Field><Field label="Competencies"><ExpandableEditor compact title={`Module ${mi+1}, lesson ${li+1} competencies`} placeholder="One competency per line" value={(item.competencies||[]).join('\n')} disabled={!course.can_manage} onChange={value=>lesson(mi,li,'competencies',value.split('\n'))}/></Field></div>{course.can_manage&&<button style={s.dangerIcon} onClick={()=>update(mi,'lessons',module.lessons.filter((_,j)=>j!==li))}><Trash2 size={14}/></button>}</div>)}
  {course.can_manage&&<div style={s.inlineActions}><button style={s.secondary} onClick={()=>update(mi,'lessons',[...(module.lessons||[]),{title:'',description:''}])}><Plus size={14}/>Lesson</button><button style={s.dangerIcon} onClick={()=>setModules(modules.filter((_,i)=>i!==mi))}><Trash2 size={14}/></button></div>}</Section>)}
  {!modules.length&&<Empty title="No curriculum yet" text="Add the first module to create the learning path."/>}</div>;
}

function CourseContent({course,documents,mutate}) {
  const [documentId,setDocumentId]=useState('');
  const [mapping,setMapping]=useState({module_id:'',lesson_id:'',start_seconds:'',end_seconds:''});
  useEffect(()=>{setDocumentId('');setMapping({module_id:'',lesson_id:'',start_seconds:'',end_seconds:''})},[course.id]);
  const available=documents;
  const selectedDocument=documents.find(item=>item.id===documentId);
  const isTimed=isTimedLearningContent(selectedDocument);
  const attach=async()=>{
    await mutate(()=>addLearningAsset(course.id,{document_id:documentId,module_id:mapping.module_id||null,lesson_id:mapping.lesson_id||null,start_seconds:isTimed&&mapping.start_seconds!==''?Number(mapping.start_seconds):null,end_seconds:isTimed&&mapping.end_seconds!==''?Number(mapping.end_seconds):null}));
    setDocumentId('');
  };
  const invalidRange=isTimed&&((mapping.start_seconds==='')!==(mapping.end_seconds==='')||(mapping.start_seconds!==''&&Number(mapping.end_seconds)<=Number(mapping.start_seconds)));
  return <div style={s.scroll}><Section title="Attach workspace content" icon={<FileText size={16}/>}><div style={s.mappingRow}><Field label="Content"><select value={documentId} onChange={e=>setDocumentId(e.target.value)}><option value="">Choose a document, audio file, or video</option>{available.map(doc=><option key={doc.id} value={doc.id}>{doc.original_name} · {doc.status}</option>)}</select></Field><MappingSelectors course={course} value={mapping} onChange={setMapping}/>{isTimed&&<TimeRangeFields value={mapping} onChange={setMapping}/>}<button style={s.primary} disabled={!course.can_manage||!documentId||invalidRange} onClick={attach}><Plus size={15}/>Attach</button></div>{isTimed&&<div style={s.rangeHint}>Optional lesson range: leave both boundaries empty to attach the complete recording.</div>}</Section>
  <div style={s.assetGrid}>{(course.assets||[]).map(asset=><AssetMappingCard key={asset.id} asset={asset} course={course} mutate={mutate}/>)}</div>
  {!course.assets?.length&&<Empty title="No course content" text="Attach processed workspace files. Embedded assets become available to the tutor and study tools."/>}</div>;
}

function AssetMappingCard({asset,course,mutate}) {
  const [mapping,setMapping]=useState({module_id:asset.module_id||'',lesson_id:asset.lesson_id||'',start_seconds:asset.start_seconds??'',end_seconds:asset.end_seconds??''});
  const [action,setAction]=useState('');
  const [feedback,setFeedback]=useState('');
  useEffect(()=>setMapping({module_id:asset.module_id||'',lesson_id:asset.lesson_id||'',start_seconds:asset.start_seconds??'',end_seconds:asset.end_seconds??''}),[asset.module_id,asset.lesson_id,asset.start_seconds,asset.end_seconds]);
  const changed=mapping.module_id!==(asset.module_id||'')||mapping.lesson_id!==(asset.lesson_id||'')||String(mapping.start_seconds)!==String(asset.start_seconds??'')||String(mapping.end_seconds)!==String(asset.end_seconds??'');
  const isTimed=isTimedLearningContent(asset);
  const invalidRange=isTimed&&((mapping.start_seconds==='')!==(mapping.end_seconds==='')||(mapping.start_seconds!==''&&Number(mapping.end_seconds)<=Number(mapping.start_seconds)));
  const payload={document_id:asset.document_id,module_id:mapping.module_id||null,lesson_id:mapping.lesson_id||null,title:asset.title||asset.original_name,start_seconds:isTimed&&mapping.start_seconds!==''?Number(mapping.start_seconds):null,end_seconds:isTimed&&mapping.end_seconds!==''?Number(mapping.end_seconds):null};
  const updateMapping=value=>{setMapping(value);setFeedback('')};
  const runAction=async mode=>{
    setFeedback('');
    if(invalidRange){setFeedback('Enter both media boundaries, and make the end greater than the start.');return}
    if(!changed){
      setFeedback(mode==='replace'?'Change the module, lesson, or media range before replacing.':'Choose a different module, lesson, or media range for the new mapping.');
      return;
    }
    setAction(mode);
    try{
      if(mode==='replace')await mutate(()=>updateLearningAsset(course.id,asset.id,payload));
      else await mutate(()=>addLearningAsset(course.id,payload));
      setFeedback(mode==='replace'?'Mapping replaced.':'New mapping added.');
    }catch(error){setFeedback(error?.message||'The mapping could not be saved.')}
    finally{setAction('')}
  };
  return <article style={s.assetMapping}>
    <header style={s.assetHeader}><div style={s.assetIcon}>{asset.file_type==='video'?'▶':'▤'}</div><div style={{...s.metaStack,flex:1}}><strong style={s.ellipsis}>{asset.title||asset.original_name}</strong><small>{asset.doc_type||asset.file_type} · {asset.status} · {asset.chunk_count||0} chunks{asset.duration_seconds?` · ${formatTime(asset.duration_seconds)}`:''}</small></div>{course.can_manage&&<button style={s.dangerIcon} onClick={()=>mutate(()=>removeLearningAsset(course.id,asset.id))} title="Remove from course"><Trash2 size={15}/></button>}</header>
    <div style={s.assetScope}><span>Current placement: <strong>{scopeLabel(course,mapping)}</strong>{asset.start_seconds!=null?` · ${formatTime(asset.start_seconds)}-${formatTime(asset.end_seconds)}`:' · complete asset'}</span></div>
    <div style={s.assetMappingControls}><MappingSelectors course={course} value={mapping} onChange={updateMapping} disabled={!course.can_manage}/>{isTimed&&<TimeRangeFields value={mapping} onChange={updateMapping} disabled={!course.can_manage}/>} {course.can_manage&&<><button type="button" style={s.secondary} disabled={Boolean(action)} onClick={()=>runAction('replace')}>{action==='replace'?<LoaderCircle size={14}/>:<Save size={14}/>} {action==='replace'?'Replacing...':'Replace Mapping'}</button><button type="button" style={s.primary} disabled={Boolean(action)} onClick={()=>runAction('add')}>{action==='add'?<LoaderCircle size={14}/>:<Plus size={14}/>} {action==='add'?'Adding...':'Add as New Mapping'}</button></>}</div>
    {course.can_manage&&<div style={feedback?s.mappingFeedback:s.rangeHint} role="status">{feedback||'Change the lesson or media range, then replace this mapping or preserve it and add another.'}</div>}
  </article>;
}

function TimeRangeFields({value,onChange,disabled=false}) { return <>
  <Field label="Media start (seconds)"><input type="number" min="0" step="0.1" disabled={disabled} placeholder="Beginning" value={value.start_seconds??''} onChange={e=>onChange({...value,start_seconds:e.target.value})}/></Field>
  <Field label="Media end (seconds)"><input type="number" min="0" step="0.1" disabled={disabled} placeholder="End" value={value.end_seconds??''} onChange={e=>onChange({...value,end_seconds:e.target.value})}/></Field>
</> }

function MappingSelectors({course,value,onChange,disabled=false}) {
  const modules=course.modules||[];
  const selectedModule=modules.find(item=>String(item.id)===String(value.module_id));
  const lessons=selectedModule?.lessons||[];
  const setModule=module_id=>onChange({...value,module_id,lesson_id:''});
  return <>
    <Field label="Module"><select disabled={disabled} value={value.module_id||''} onChange={e=>setModule(e.target.value)}><option value="">Entire course</option>{modules.map((module,index)=><option key={module.id} value={module.id}>Module {index+1}: {module.title}</option>)}</select></Field>
    <Field label="Lesson"><select disabled={disabled||!value.module_id} value={value.lesson_id||''} onChange={e=>onChange({...value,lesson_id:e.target.value})}><option value="">All lessons in module</option>{lessons.map((lesson,index)=><option key={lesson.id} value={lesson.id}>Lesson {index+1}: {lesson.title}</option>)}</select></Field>
  </>;
}

function ScopeBar({course,scope,setScope,count}) {
  return <div style={s.scopeBar}><div style={s.metaStack}><strong>{course.course_code?`${course.course_code} · `:''}{course.title}</strong><small>Learning scope · {count} embedded {count===1?'asset':'assets'} available</small></div><div style={s.scopeSelectors}><MappingSelectors course={course} value={scope} onChange={setScope}/></div></div>;
}

function scopedDocumentIds(course,scope) {
  return [...new Set((course.assets||[]).filter(asset=>{
    if(asset.status!=='embedded')return false;
    if(!scope.module_id)return true;
    if(scope.lesson_id){
      if(!asset.module_id)return true;
      if(String(asset.module_id)!==String(scope.module_id))return false;
      return !asset.lesson_id||String(asset.lesson_id)===String(scope.lesson_id);
    }
    if(!asset.module_id)return true;
    if(String(asset.module_id)!==String(scope.module_id))return false;
    return true;
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

function Tutor({course,workspaceId}) {
  const [messages,setMessages]=useState([]); const [input,setInput]=useState(''); const [thinking,setThinking]=useState(false); const [sessionId,setSessionId]=useState('');
  const [scope,setScope]=useState({module_id:'',lesson_id:''});
  const [composerError,setComposerError]=useState('');
  const [language,setLanguage]=useState('auto'); const [voiceMode,setVoiceMode]=useState('idle'); const [voiceStatus,setVoiceStatus]=useState('');
  const recognitionRef=useRef(null); const recorderRef=useRef(null); const streamRef=useRef(null); const audioChunksRef=useRef([]); const spokenRef=useRef('');
  useEffect(()=>setScope({module_id:'',lesson_id:''}),[course.id]);
  const documentIds=useMemo(()=>scopedDocumentIds(course,scope),[course.assets,scope.module_id,scope.lesson_id]);
  const documentSignature=useMemo(()=>[...documentIds].sort().join(','),[documentIds]);
  const sessionKey=`learning_session_${LEARNING_SESSION_SCOPE_VERSION}_${course.id}_${scope.module_id||'course'}_${scope.lesson_id||'all'}`;
  useEffect(()=>{let active=true;setMessages([]);setSessionId('');setComposerError('');const id=localStorage.getItem(sessionKey);if(id)getSession(id).then(session=>{if(!active)return;const sessionSignature=[...(session.document_ids||[])].sort().join(',');if(sessionSignature!==documentSignature){localStorage.removeItem(sessionKey);return}setSessionId(id);setMessages(session.messages||[])}).catch(()=>localStorage.removeItem(sessionKey));return()=>{active=false}},[sessionKey,documentSignature]);
  useEffect(()=>()=>{
    recognitionRef.current?.abort?.();
    if(recorderRef.current?.state==='recording')recorderRef.current.stop();
    streamRef.current?.getTracks?.().forEach(track=>track.stop());
  },[]);
  const ask=async question=>{
    const content=String(question||'').trim();
    if(!content||thinking)return;
    if(!documentIds.length){setComposerError(scope.lesson_id?'Attach embedded content to this lesson, its module, or the course before asking the Tutor.':'Attach and embed content in the selected learning scope before asking the Tutor.');return}
    setThinking(true);setVoiceStatus('');setComposerError('');
    const user={role:'user',content};const assistant={role:'assistant',content:'',sources:null};const base=[...messages,user];
    setMessages([...base,assistant]);setInput('');let sid=sessionId;
    try{
      const resolved=await resolveLearningScope(course.id,scope.module_id||null,scope.lesson_id||null);
      if(String(resolved.course_id)!==String(course.id))throw new Error('The Tutor received a stale course scope. Select the course again and retry.');
      if(!resolved.document_ids?.length)throw new Error('No embedded content is available in this learning scope.');
      if(!sid){const created=await createSession(`${course.title} · ${scopeLabel(course,scope)} · Learning`,resolved.document_ids,resolved.workspace_id||workspaceId);sid=created.id;setSessionId(sid);localStorage.setItem(sessionKey,sid)}
      let answer='';
      await streamChat({question:`${resolved.instruction}\n\nSTUDENT QUESTION:\n${user.content}`,documentIds:resolved.document_ids,history:messages.slice(-10).map(({role,content:messageContent})=>({role,content:messageContent})),workspaceId:resolved.workspace_id||workspaceId,responseLanguage:language==='auto'?null:language.split('-')[0],evidenceRanges:resolved.evidence_ranges||[]},{
        onToken:t=>{answer+=t;setMessages([...base,{...assistant,content:answer}])},
        onDone:async sources=>{const done=[...base,{...assistant,content:answer,sources:sources||[],learning_boundary:resolved.label,scope_type:resolved.scope_type}];setMessages(done);await saveSessionMessages(sid,done);setThinking(false)},
        onError:e=>{setMessages([...base,{...assistant,content:`Unable to answer: ${e}`}]);setThinking(false)},
      });
    }catch(e){setMessages([...base,{...assistant,content:`Unable to answer: ${e.message||e}`}]);setComposerError(e.message||String(e));setThinking(false)}
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
  const emptyScopeText=scope.lesson_id
    ? 'No embedded content is mapped directly to this lesson.'
    : 'No embedded content is mapped to this curriculum scope.';
  return <div style={s.tutor}><ScopeBar course={course} scope={scope} setScope={setScope} count={documentIds.length}/><div style={s.messages}>{!messages.length&&<Empty title="Ask the course" text={documentIds.length?'Questions use embedded content available to the selected curriculum scope.':emptyScopeText}/>} {messages.map((message,index)=><article key={index} style={{...s.message,...(message.role==='user'?s.userMessage:s.aiMessage)}}><strong>{message.role==='user'?'You':'DocIntel Tutor'}</strong>{message.role==='user'?<div style={s.messageText}>{message.content}</div>:<MarkdownRenderer text={message.content||''} style={s.tutorAnswer}/>} {message.role==='assistant'&&<EvidenceSources courseId={course.id} sources={message.sources||[]} boundary={message.learning_boundary||scopeLabel(course,scope)}/>}</article>)}{thinking&&<div style={s.loading}><LoaderCircle size={14}/> Retrieving and reasoning...</div>}</div><form style={s.composer} onSubmit={e=>{e.preventDefault();ask(input)}}><select value={language} onChange={e=>setLanguage(e.target.value)} style={s.tutorLanguage} disabled={voiceActive||voiceMode==='transcribing'} aria-label="Tutor language" title="Question and response language">{TUTOR_LANGUAGES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><textarea value={input} onChange={e=>{setInput(e.target.value);if(composerError)setComposerError('')}} onKeyDown={e=>{const composing=e.isComposing||e.nativeEvent?.isComposing;if(e.key==='Enter'&&!e.shiftKey&&!composing){e.preventDefault();e.currentTarget.form?.requestSubmit()}}} placeholder="Ask a grounded question about this course..." disabled={thinking}/><button type="button" style={{...s.voiceButton,...(voiceActive?s.voiceButtonOn:{})}} onClick={toggleVoice} disabled={thinking||voiceMode==='transcribing'||!documentIds.length} title={voiceActive?'Stop voice input':'Ask with voice'} aria-label={voiceActive?'Stop voice input':'Ask with voice'}>{voiceMode==='transcribing'?<LoaderCircle size={17}/>:voiceActive?<Square size={15}/>:<Mic size={18}/>}</button><button type="submit" style={s.send} disabled={thinking||!input.trim()} title="Send"><Send size={18}/></button>{voiceStatus&&<div style={s.voiceStatus} role="status">{voiceStatus}</div>}{composerError&&<div style={s.voiceStatus} role="alert">{composerError}</div>}</form></div>;
}

function EvidenceSources({courseId,sources,boundary}) {
  const openEvidence=async source=>{
    if(!source.document_id)return;
    try{const result=await getLearningEvidenceUrl(courseId,source.document_id);const suffix=source.start_seconds!=null?`#t=${Math.max(0,Math.floor(source.start_seconds))}`:'';window.open(`${result.url}${suffix}`,'_blank','noopener,noreferrer')}catch(error){alert(error.message||String(error))}
  };
  return <div style={s.evidenceBlock}><div style={s.boundary}><BookOpen size={13}/><span>Learning boundary: {boundary}</span></div>{sources.length?<details open style={s.sources}><summary>Supporting evidence ({sources.length})</summary><div style={s.evidenceGrid}>{sources.map((source,index)=><article key={`${source.document_id||'source'}-${index}`} style={s.evidenceCard}><header><strong>[{source.source_number||index+1}] {source.doc_name||source.original_name||source.filename||`Source ${index+1}`}</strong><small>{source.start_seconds!=null?`${formatTime(source.start_seconds)}${source.end_seconds!=null?` - ${formatTime(source.end_seconds)}`:''}`:`Chunk ${(source.chunk_index??0)+1}`}</small></header><p>{source.excerpt||source.preview||'Evidence excerpt was not returned.'}</p><button type="button" style={s.linkBtn} disabled={!source.document_id} onClick={()=>openEvidence(source)}><ExternalLink size={13}/>{source.start_seconds!=null?'Jump to timestamp':'Open document'}</button></article>)}</div></details>:<div style={s.noEvidence}><AlertTriangle size={13}/>No supporting evidence was returned for this answer.</div>}</div>;
}

function StudyTools({course,workspaceId,mutate}) {
  const [type,setType]=useState('study_guide'); const [generating,setGenerating]=useState(false); const [scope,setScope]=useState({module_id:'',lesson_id:''}); const [toolError,setToolError]=useState('');
  useEffect(()=>setScope({module_id:'',lesson_id:''}),[course.id]);
  const ids=useMemo(()=>scopedDocumentIds(course,scope),[course.assets,scope.module_id,scope.lesson_id]);
  const selectedScope=scopeLabel(course,scope);
  const generateContent=(prompt,resolved)=>new Promise((resolve,reject)=>{let content='';const scopedPrompt=`${resolved.instruction}\n\nTASK:\n${prompt}`;streamChat({question:scopedPrompt,documentIds:resolved.document_ids,history:[],workspaceId:resolved.workspace_id||workspaceId,evidenceRanges:resolved.evidence_ranges||[]},{onToken:t=>content+=t,onDone:()=>resolve(content),onError:reject})});
  const generate=async()=>{
    if(!ids.length)return;
    setGenerating(true);setToolError('');
    try{
      const resolved=await resolveLearningScope(course.id,scope.module_id||null,scope.lesson_id||null);
      if(!resolved.document_ids?.length)throw new Error('No embedded content is available in this learning scope.');
      let content=await generateContent(STUDY_PROMPTS[type],resolved);
      if(type==='practice_questions'){
        try{content=JSON.stringify(parseGeneratedQuiz(content))}
        catch(firstError){
          console.warn('Retrying malformed practice quiz generation',firstError);
          content=JSON.stringify(parseGeneratedQuiz(await generateContent(PRACTICE_RETRY_PROMPT,resolved)));
        }
      }
      await mutate(()=>saveLearningArtifact(course.id,{artifact_type:type,title:`${pretty(type)} · ${selectedScope}`,content,source_document_ids:resolved.document_ids,module_id:resolved.module_id,lesson_id:resolved.lesson_id}));
    }catch(e){
      console.error(e);setToolError(e.message||String(e));
    }finally{setGenerating(false)}
  };
  return <div style={s.scroll}><ScopeBar course={course} scope={scope} setScope={setScope} count={ids.length}/><div style={s.studyTarget}><strong>Generation target</strong><span>{selectedScope}</span></div><div style={s.toolbar}><p>Generate source-grounded learning material from the selected curriculum scope.</p><select value={type} onChange={e=>setType(e.target.value)}>{Object.keys(STUDY_PROMPTS).map(x=><option key={x} value={x}>{pretty(x)}</option>)}</select><button style={s.primary} disabled={generating||!ids.length} onClick={generate}><Sparkles size={15}/>{generating?'Generating...':'Generate'}</button></div>
  {toolError&&<div style={s.error}>Practice questions could not be generated in the required interactive format. {toolError}</div>}
  <div style={s.artifactGrid}>{(course.artifacts||[]).map(item=>{const interactive=['practice_questions','flashcards'].includes(item.artifact_type);const attempt=(course.quiz_attempts||[]).find(row=>row.artifact_id===item.id);return <article style={{...s.artifact,...(interactive?s.quizArtifact:{})}} key={item.id}><header style={s.artifactHeader}><div style={s.metaStack}><strong>{item.title||pretty(item.artifact_type)}</strong><small>{new Date(item.created_at).toLocaleString()}</small></div><button style={s.dangerIcon} onClick={()=>mutate(()=>deleteLearningArtifact(course.id,item.id))}><Trash2 size={14}/></button></header>{item.artifact_type==='practice_questions'?<PracticeQuiz courseId={course.id} artifact={item} attempt={attempt}/>:item.artifact_type==='flashcards'?<FlashcardDeck content={item.content}/>:<ExpandableMarkdown title={item.title||pretty(item.artifact_type)} text={item.content}/>}</article>})}</div>{!course.artifacts?.length&&<Empty title="No saved study materials" text="Generate a summary, study guide, key concepts, flashcards, or practice questions."/>}</div>;
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

function PracticeQuiz({courseId,artifact,attempt}) {
  const mobile=useMobile();
  const quiz=useMemo(()=>storedQuiz(artifact.content),[artifact.content]);
  const [selections,setSelections]=useState(attempt?.answers||{}); const [submitted,setSubmitted]=useState(attempt?.result||{}); const [saving,setSaving]=useState(false); const [saveError,setSaveError]=useState('');
  useEffect(()=>{setSelections(attempt?.answers||{});setSubmitted(attempt?.result||{});setSaveError('')},[artifact.id,attempt?.updated_at]);
  if(!quiz)return <ExpandableText title="Practice Questions" text={artifact.content}/>;
  const toggle=(questionId,optionId)=>setSelections(current=>{
    const selected=new Set(current[questionId]||[]);
    selected.has(optionId)?selected.delete(optionId):selected.add(optionId);
    return {...current,[questionId]:[...selected]};
  });
  const result=question=>Boolean(submitted[question.id]?.is_correct);
  const completed=quiz.questions.filter(question=>submitted[question.id]);
  const correctCount=completed.filter(result).length;
  const reset=async()=>{setSaving(true);setSaveError('');try{await saveLearningQuizAttempt(courseId,artifact.id,{},true);setSelections({});setSubmitted({})}catch(e){setSaveError(e.message||String(e))}finally{setSaving(false)}};
  const submit=async question=>{setSaving(true);setSaveError('');try{const saved=await saveLearningQuizAttempt(courseId,artifact.id,{[question.id]:selections[question.id]||[]});setSelections(saved.answers||{});setSubmitted(saved.result||{})}catch(e){setSaveError(e.message||String(e))}finally{setSaving(false)}};
  return <div style={s.quiz}>
    <div style={s.quizSummary}><div style={s.metaStack}><strong>Interactive practice</strong><small>{quiz.instructions}</small></div><div style={s.quizScore}><span>{correctCount} correct</span><span>{completed.length} of {quiz.questions.length} submitted</span></div>{completed.length>0&&<button style={s.secondary} onClick={reset}><RotateCcw size={14}/>Try again</button>}</div>
    {quiz.questions.map((question,index)=>{const done=Boolean(submitted[question.id]);const correct=done&&result(question);const correctOptions=question.options.filter(option=>option.correct);return <section key={question.id} style={s.quizQuestion}>
      <header style={s.quizQuestionHeader}><span style={s.questionNumber}>{index+1}</span><strong>{question.question}</strong>{done&&(correct?<span style={s.correctBadge}><CheckCircle2 size={15}/>Correct</span>:<span style={s.incorrectBadge}><XCircle size={15}/>Incorrect</span>)}</header>
      <div style={{...s.quizOptions,...(mobile?{gridTemplateColumns:'1fr'}:{})}}>{question.options.map(option=>{const selected=(selections[question.id]||[]).includes(option.id);const answerStyle=done&&option.correct?s.correctOption:done&&selected&&!option.correct?s.incorrectOption:{};return <label key={option.id} style={{...s.quizOption,...answerStyle}}><input type="checkbox" checked={selected} disabled={done} onChange={()=>toggle(question.id,option.id)}/><b>{option.id}</b><span>{option.text}</span>{done&&option.correct&&<CheckCircle2 size={16}/>}</label>})}</div>
      {!done?<button style={s.primary} disabled={saving||!(selections[question.id]||[]).length} onClick={()=>submit(question)}>{saving?'Saving...':'Submit answer'}</button>:<div style={{...s.answerFeedback,...(correct?s.correctFeedback:s.incorrectFeedback)}}><strong>{correct?'Correct answer':'Correct answer shown below'}</strong><span>{correctOptions.map(option=>`${option.id}. ${option.text}`).join(' | ')}</span><p>{question.explanation}</p></div>}
    </section>})}
    {saveError&&<div style={s.error}>{saveError}</div>}
  </div>;
}

function Assignments({course,documents,workspaceId,mutate}) {
  const mobile=useMobile();
  const [draft,setDraft]=useState({title:'',description:'',assignment_type:'written',module_id:'',lesson_id:'',due_at:'',publication_status:'draft',rubric:[{id:'quality',title:'Quality and accuracy',description:'Demonstrates accurate, evidence-backed understanding.',weight:100}]});
  const [working,setWorking]=useState('');
  const modules=course.modules||[];
  const lessons=modules.find(item=>String(item.id)===String(draft.module_id))?.lessons||[];
  const updateCriterion=(index,key,value)=>setDraft(current=>({...current,rubric:current.rubric.map((item,itemIndex)=>itemIndex===index?{...item,[key]:key==='weight'?Number(value):value}:item)}));
  const addCriterion=()=>setDraft(current=>({...current,rubric:[...current.rubric,{id:`criterion_${current.rubric.length+1}`,title:'',description:'',weight:0}]}));
  const removeCriterion=index=>setDraft(current=>({...current,rubric:current.rubric.filter((_,itemIndex)=>itemIndex!==index)}));
  const create=async()=>{setWorking('create');try{await mutate(()=>createLearningAssignment(course.id,{...draft,module_id:draft.module_id||null,lesson_id:draft.lesson_id||null,due_at:draft.due_at?new Date(draft.due_at).toISOString():null,max_score:100,source_document_ids:[]}));setDraft({...draft,title:'',description:'',due_at:''})}finally{setWorking('')}};
  return <div style={s.scroll}>{course.can_manage&&<Section title="Create assignment or project" icon={<ClipboardList size={16}/>}><div style={s.grid}><Field label="Title"><input value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})}/></Field><Field label="Type"><select value={draft.assignment_type} onChange={e=>setDraft({...draft,assignment_type:e.target.value})}>{['written','document','presentation','project'].map(value=><option key={value} value={value}>{pretty(value)}</option>)}</select></Field><Field label="Module"><select value={draft.module_id} onChange={e=>setDraft({...draft,module_id:e.target.value,lesson_id:''})}><option value="">Course-wide</option>{modules.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></Field><Field label="Lesson"><select value={draft.lesson_id} onChange={e=>setDraft({...draft,lesson_id:e.target.value})}><option value="">Any lesson</option>{lessons.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></Field><Field label="Due"><input type="datetime-local" value={draft.due_at} onChange={e=>setDraft({...draft,due_at:e.target.value})}/></Field><Field label="Publication"><select value={draft.publication_status} onChange={e=>setDraft({...draft,publication_status:e.target.value})}><option value="draft">Draft</option><option value="published">Published</option></select></Field><Field label="Instructions" wide><ExpandableEditor title="Assignment instructions" value={draft.description} onChange={value=>setDraft({...draft,description:value})}/></Field><Field label="Rubric criteria (weights must total 100)" wide><div style={s.rubricList}>{draft.rubric.map((item,index)=><div key={item.id} style={{...s.rubricRow,...(mobile?{gridTemplateColumns:'1fr 72px 30px'}:{})}}><input style={mobile?{gridColumn:'1/-1'}:{}} placeholder="Criterion" value={item.title} onChange={e=>updateCriterion(index,'title',e.target.value)}/><input placeholder="Evidence expectations" value={item.description} onChange={e=>updateCriterion(index,'description',e.target.value)}/><input aria-label="Criterion weight" type="number" min="1" max="100" value={item.weight} onChange={e=>updateCriterion(index,'weight',e.target.value)}/><button style={s.dangerIcon} disabled={draft.rubric.length===1} onClick={()=>removeCriterion(index)}><Trash2 size={14}/></button></div>)}<button style={s.secondary} onClick={addCriterion}><Plus size={14}/>Add criterion</button></div></Field></div><button style={s.primary} disabled={!draft.title.trim()||working==='create'||draft.rubric.reduce((sum,item)=>sum+Number(item.weight||0),0)!==100} onClick={create}><Plus size={15}/>Create assignment</button></Section>}
  <div style={s.assignmentGrid}>{(course.assignments||[]).map(assignment=><AssignmentCard key={assignment.id} assignment={assignment} course={course} documents={documents} workspaceId={workspaceId} mutate={mutate}/>)}</div>{!course.assignments?.length&&<Empty title="No assignments yet" text="Teachers can publish written work, uploads, recorded presentations, and projects with reviewable rubrics."/>}</div>;
}

function AssignmentCard({assignment,course,documents,workspaceId,mutate}) {
  const existing=(course.submissions||[]).find(item=>String(item.assignment_id)===String(assignment.id)&&(!course.can_manage||String(item.user_id)===String(course.members?.find(member=>member.persona==='student'&&member.user_id===item.user_id)?.user_id)));
  const [text,setText]=useState(existing?.submission_text||''); const [documentIds,setDocumentIds]=useState(existing?.document_ids||[]); const [presentationId,setPresentationId]=useState(existing?.presentation_document_id||'');
  const [busy,setBusy]=useState(false); const [uploading,setUploading]=useState(false);
  useEffect(()=>{setText(existing?.submission_text||'');setDocumentIds(existing?.document_ids||[]);setPresentationId(existing?.presentation_document_id||'')},[existing?.id,existing?.updated_at]);
  const submissions=(course.submissions||[]).filter(item=>String(item.assignment_id)===String(assignment.id));
  const save=async submit=>{setBusy(true);try{await mutate(()=>saveLearningSubmission(course.id,assignment.id,{submission_text:text,document_ids:documentIds,presentation_document_id:presentationId||null,submit}))}finally{setBusy(false)}};
  const upload=async event=>{const files=[...event.target.files];if(!files.length)return;setUploading(true);try{const result=await uploadDocuments(files,workspaceId);const ids=(result.uploaded||[]).map(item=>item.doc_id);setDocumentIds(current=>[...new Set([...current,...ids])])}catch(error){alert(error.message||String(error))}finally{setUploading(false);event.target.value=''}};
  return <article style={s.assignment}><header style={s.assignmentHeader}><div style={s.metaStack}><strong>{assignment.title}</strong><small>{pretty(assignment.assignment_type)} · {assignment.lesson_title||assignment.module_title||'Course-wide'} · {assignment.publication_status}</small></div>{course.can_manage&&<button style={s.dangerIcon} title="Delete assignment" onClick={()=>confirm('Delete this assignment and all submissions?')&&mutate(()=>deleteLearningAssignment(course.id,assignment.id))}><Trash2 size={15}/></button>}</header><ExpandableText title={assignment.title} text={assignment.description||'No additional instructions.'}/>{assignment.due_at&&<small>Due {new Date(assignment.due_at).toLocaleString()}</small>}
  {!course.can_manage&&<div style={s.submissionBox}><Field label="Written response"><ExpandableEditor title="Written submission" value={text} onChange={setText}/></Field><Field label="Supporting documents"><div style={s.documentChecks}>{documents.filter(item=>item.status==='embedded').map(item=><label key={item.id}><input type="checkbox" checked={documentIds.includes(item.id)} onChange={e=>setDocumentIds(current=>e.target.checked?[...new Set([...current,item.id])]:current.filter(id=>id!==item.id))}/>{item.original_name}</label>)}</div></Field><Field label="Recorded presentation"><select value={presentationId} onChange={e=>setPresentationId(e.target.value)}><option value="">None</option>{documents.filter(isTimedLearningContent).map(item=><option key={item.id} value={item.id}>{item.original_name}</option>)}</select></Field><label style={s.uploadButton}><Upload size={14}/>{uploading?'Uploading...':'Upload assignment files'}<input hidden multiple type="file" onChange={upload}/></label><div style={s.inlineActions}><button style={s.secondary} disabled={busy} onClick={()=>save(false)}><Save size={14}/>Save draft</button><button style={s.primary} disabled={busy} onClick={()=>save(true)}><Send size={14}/>Submit</button></div>{existing&&<small>Status: {pretty(existing.status)} · Revision {existing.revision_number}{existing.score!=null?` · ${existing.score}/${assignment.max_score}`:''}</small>}{existing?.instructor_feedback&&<div style={s.feedback}>{existing.instructor_feedback}</div>}{existing?.revisions?.length>0&&<RevisionHistory revisions={existing.revisions}/>}</div>}
  {course.can_manage&&<div style={s.reviewList}>{submissions.map(submission=><SubmissionReviewCard key={submission.id} submission={submission} assignment={assignment} course={course} mutate={mutate}/>)}</div>}
  </article>;
}

function SubmissionReviewCard({submission,assignment,course,mutate}) {
  const [feedback,setFeedback]=useState(submission.instructor_feedback||''); const [score,setScore]=useState(submission.score??'');
  const [evaluating,setEvaluating]=useState(false); const [evaluationError,setEvaluationError]=useState('');
  useEffect(()=>{setFeedback(submission.instructor_feedback||'');setScore(submission.score??'')},[submission.id,submission.updated_at]);
  const review=status=>mutate(()=>reviewLearningSubmission(course.id,assignment.id,submission.id,{status,instructor_feedback:feedback,score:score===''?null:Number(score)}));
  const evaluate=async()=>{
    setEvaluating(true);setEvaluationError('');
    try {
      const result=await mutate(()=>evaluateLearningSubmission(course.id,assignment.id,submission.id));
      const suggested=result?.ai_evaluation?.overall_score;
      if(suggested!=null&&score==='')setScore(String(suggested));
    } catch(error) {
      setEvaluationError(error.message||String(error));
    } finally {
      setEvaluating(false);
    }
  };
  const evaluationFailed=submission.ai_evaluation?.status==='needs_human_review';
  const canEvaluate=submission.status==='submitted'||(submission.status==='in_review'&&evaluationFailed);
  const canReview=['submitted','in_review'].includes(submission.status);
  return <article style={s.reviewCard}>
    <strong>{submission.full_name||submission.email}</strong>
    <small>{pretty(submission.status)} · Revision {submission.revision_number}</small>
    <ExpandableText title="Submission" text={submission.submission_text||'Uploaded evidence only.'}/>
    {submission.revisions?.length>0&&<RevisionHistory revisions={submission.revisions}/>}
    {submission.ai_evaluation?.summary&&<AiRubricResult evaluation={submission.ai_evaluation} maxScore={assignment.max_score}/>}
    {evaluationError&&<div style={s.error}>{evaluationError}</div>}
    <div style={s.inlineActions}>
      <button style={s.secondary} disabled={evaluating||!canEvaluate} onClick={evaluate} title={canEvaluate?'Evaluate the submitted work':'Submit the work before running the rubric'}>
        {evaluating?<LoaderCircle size={14} className="spin"/>:<Sparkles size={14}/>} {evaluating?'Evaluating...':evaluationFailed?'Retry AI rubric':submission.ai_evaluation?.summary?'Evaluated':'AI rubric'}
      </button>
      <input aria-label="Instructor score" style={s.scoreInput} type="number" min="0" max={assignment.max_score} placeholder={`/${assignment.max_score}`} value={score} onChange={e=>setScore(e.target.value)}/>
    </div>
    {!canEvaluate&&!submission.ai_evaluation?.summary&&<small>AI rubric becomes available after the learner submits the assignment.</small>}
    <ExpandableEditor compact title="Instructor feedback" value={feedback} onChange={setFeedback}/>
    <div style={s.inlineActions}><button style={s.secondary} disabled={!canReview} onClick={()=>review('revision_requested')}>Request revision</button><button style={s.primary} disabled={!canReview} onClick={()=>review('approved')}><CheckCircle2 size={14}/>Approve</button></div>
  </article>;
}

function AiRubricResult({evaluation,maxScore}) {
  const criteria=Array.isArray(evaluation.criteria)?evaluation.criteria:[];
  const strengths=Array.isArray(evaluation.strengths)?evaluation.strengths:[];
  const improvements=Array.isArray(evaluation.improvements)?evaluation.improvements:[];
  const score=evaluation.overall_score;
  return <div style={s.aiEvaluation}>
    <div style={s.evaluationHeader}><span><Sparkles size={15}/>AI rubric · {pretty(evaluation.status||'completed')}</span><strong style={s.evaluationScore}>{score==null?'Human review needed':`${score}/${maxScore}`}</strong></div>
    <p>{evaluation.summary}</p>
    {criteria.length>0&&<div style={s.evaluationCriteria}>{criteria.map((item,index)=><article key={item.criterion_id||index} style={s.evaluationCriterion}>
      <div style={s.evaluationHeader}><strong>{pretty(item.criterion_id||`Criterion ${index+1}`)}</strong><span>{item.score??'Not scored'} / {item.max_score??'Not scored'}</span></div>
      {item.feedback&&<p>{item.feedback}</p>}
      {item.evidence&&<small style={s.evaluationEvidence}>Evidence: {Array.isArray(item.evidence)?item.evidence.join(' · '):String(item.evidence)}</small>}
    </article>)}</div>}
    {(strengths.length>0||improvements.length>0)&&<details style={s.evaluationDetails}><summary>Strengths and improvements</summary>{strengths.length>0&&<p><strong>Strengths:</strong> {strengths.join(' · ')}</p>}{improvements.length>0&&<p><strong>Improve:</strong> {improvements.join(' · ')}</p>}</details>}
    {evaluation.error&&<details style={s.evaluationDiagnostic}><summary>Why automated evaluation was unavailable</summary><code>{evaluation.error}</code></details>}
    <small>Advisory evaluation only. The instructor controls feedback, score, revision, and approval.</small>
  </div>;
}

function RevisionHistory({revisions}) { return <details style={s.sources}><summary>Revision history ({revisions.length})</summary>{revisions.map(item=><div key={item.id}>Revision {item.revision_number} · {pretty(item.status)} · {new Date(item.created_at).toLocaleString()}</div>)}</details> }

function InstructorDashboard({course}) {
  const [data,setData]=useState(null); const [loading,setLoading]=useState(false); const [error,setError]=useState('');
  const load=async()=>{setLoading(true);setError('');try{setData(await getLearningInstructorDashboard(course.id))}catch(e){setError(e.message||String(e))}finally{setLoading(false)}};
  useEffect(()=>{if(course.can_manage)load()},[course.id]);
  if(!course.can_manage)return <Empty title="Instructor access required" text="Cohort intelligence is available to course teachers and administrators."/>;
  const summary=data?.summary||{};
  return <div style={s.scroll}><div style={s.progressToolbar}><div><h3 style={s.progressTitle}><BarChart3 size={17}/>Instructor Intelligence</h3><p>Evidence-backed cohort signals for intervention and content improvement.</p></div><button style={s.secondary} onClick={load} disabled={loading}><RefreshCw size={14}/>Refresh</button></div>{loading&&<div style={s.loading}><LoaderCircle size={14}/>Calculating cohort signals...</div>}{error&&<div style={s.error}>{error}</div>}{data&&<><div style={s.metricGrid}><Metric label="Cohort progress" value={`${summary.cohort_progress_pct||0}%`} detail={`${summary.student_count||0} learners`}/><Metric label="Assessment performance" value={summary.assessment_performance_pct==null?'Not assessed':`${summary.assessment_performance_pct}%`} detail="Across graded attempts"/><Metric label="Average engagement" value={`${Math.round((summary.average_engagement_seconds||0)/60)} min`} detail="Saved lesson activity"/><Metric label="At-risk learners" value={summary.at_risk_count||0} detail="Needs instructor attention"/><Metric label="Unanswered questions" value={summary.open_question_count||0} detail="Awaiting response"/></div><Section title="Learner health" icon={<GraduationCap size={16}/>}><div style={s.dashboardTable}>{(data.learners||[]).map(item=><div key={item.user_id} style={s.dashboardRow}><strong>{item.full_name||item.email}</strong><span>{item.progress_pct}% progress</span><span>{item.assessment_pct==null?'Not assessed':`${item.assessment_pct}% assessed`}</span><span style={item.at_risk?s.riskText:s.goodText}>{item.at_risk?item.risk_reasons.join(' · '):'On track'}</span></div>)}</div></Section><div style={s.dashboardColumns}><Section title="Difficult concepts" icon={<AlertTriangle size={16}/>}><InsightList rows={data.difficult_concepts} empty="No below-threshold lesson concepts." render={item=>`${item.title} · ${item.assessment_pct}%`}/></Section><Section title="Content-quality gaps" icon={<FileText size={16}/>}><InsightList rows={data.content_quality_gaps} empty="No lesson evidence gaps." render={item=>`${item.title}: ${item.gap}`}/></Section><Section title="Unanswered questions" icon={<CircleHelp size={16}/>}><InsightList rows={data.unanswered_questions} empty="No open learner questions." render={item=>item.question}/></Section><Section title="Assignment performance" icon={<ClipboardList size={16}/>}><InsightList rows={data.assignment_performance} empty="No published assignments." render={item=>`${item.title}: ${item.submitted_count} submitted · ${item.approved_count} approved`}/></Section></div></>}</div>;
}

function InsightList({rows=[],empty,render}) { return rows.length?<div style={s.insightList}>{rows.map((item,index)=><div key={item.id||item.lesson_id||item.assignment_id||index}>{render(item)}</div>)}</div>:<small>{empty}</small> }

function LearningProgress({course}) {
  const canReview=course.can_manage||['teacher','advisor','admin'].includes(course.my_persona);
  const learners=(course.members||[]).filter(member=>member.persona==='student');
  const reviewerDefault=canReview&&course.my_persona!=='student'?(learners[0]?.user_id||''):'';
  const [learnerId,setLearnerId]=useState('');
  const [mastery,setMastery]=useState(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [savingLesson,setSavingLesson]=useState('');
  const load=async(target=learnerId)=>{setLoading(true);setError('');try{setMastery(await getLearningMastery(course.id,target||''))}catch(e){setMastery(null);setError(e.message||String(e))}finally{setLoading(false)}};
  useEffect(()=>{setLearnerId(reviewerDefault);load(reviewerDefault)},[course.id]);
  const selectLearner=value=>{setLearnerId(value);load(value)};
  const saveProgress=async(lesson,status,progressPct)=>{
    setSavingLesson(lesson.id);setError('');
    try{
      await updateLearningLessonProgress(course.id,lesson.id,{status,progress_pct:progressPct,time_spent_seconds:lesson.time_spent_seconds||0,last_position_seconds:lesson.last_position_seconds??null});
      await load(learnerId);
    }catch(e){setError(e.message||String(e))}finally{setSavingLesson('')}
  };
  const summary=mastery?.summary||{};
  return <div style={s.scroll}>
    <div style={s.progressToolbar}>
      <div><h3 style={s.progressTitle}><BarChart3 size={17}/>Learner progress and mastery</h3><p>Completion comes from saved lesson activity. Mastery comes only from graded practice evidence.</p></div>
      <div style={s.progressActions}>{canReview&&learners.length>0&&<label style={s.inlineField}><span>Learner</span><select value={learnerId} onChange={e=>selectLearner(e.target.value)}>{course.my_persona==='student'&&<option value="">My progress</option>}{learners.map(member=><option key={member.user_id} value={member.user_id}>{member.full_name||member.email}</option>)}</select></label>}<button style={s.secondary} onClick={()=>load()} disabled={loading}><RefreshCw size={14}/>Refresh</button></div>
    </div>
    {loading&&<div style={s.loading}><LoaderCircle size={14} className="spin"/>Calculating learning state...</div>}
    {error&&<div style={s.error}>{error}</div>}
    {mastery&&<>
      <div style={s.metricGrid}>
        <Metric label="Course completion" value={`${summary.completion_pct||0}%`} detail={`${summary.completed_lessons||0} of ${summary.lesson_count||0} lessons`}/>
        <Metric label="Learning progress" value={`${summary.progress_pct||0}%`} detail="Includes lessons in progress"/>
        <Metric label="Documented mastery" value={summary.mastery_pct==null?'Not assessed':`${summary.mastery_pct}%`} detail={`${summary.mastered_lessons||0} mastered · ${summary.assessed_lessons||0} assessed`}/>
        <Metric label="Mastery threshold" value={`${mastery.passing_score}%`} detail="Configured course passing score"/>
      </div>
      <Section title="Recommended next actions" icon={<Sparkles size={16}/> }>
        <div style={s.recommendationGrid}>{(mastery.recommendations||[]).map((item,index)=><article style={s.recommendation} key={`${item.type}-${item.lesson_id||index}`}><span style={s.recommendationNumber}>{index+1}</span><div><strong>{item.title}</strong><p>{item.reason}</p><small>{item.action}</small></div></article>)}</div>
      </Section>
      {(mastery.modules||[]).map(module=><Section key={module.id} title={module.title} icon={<BookOpen size={16}/> }>
        <div style={s.moduleSummary}><span>{module.completed_lessons} / {module.lesson_count} lessons complete</span><span>{module.progress_pct}% progress</span><span>{module.mastery_pct==null?'Not assessed':`${module.mastery_pct}% mastery`}</span></div>
        <div style={s.masteryLessons}>{(module.lessons||[]).map(lesson=><article style={s.masteryLesson} key={lesson.id}>
          <div style={s.masteryLessonHeader}><div style={s.metaStack}><strong>{lesson.title}</strong><small>{pretty(lesson.status)} · {lesson.progress_pct}% complete</small></div><span style={{...s.masteryBadge,...(lesson.mastery_status==='mastered'?s.masteredBadge:lesson.mastery_status==='developing'?s.developingBadge:{})}}>{lesson.assessment_score==null?'Not assessed':`${lesson.assessment_score}% · ${pretty(lesson.mastery_status)}`}</span></div>
          {lesson.objectives?.length>0&&<p style={s.masteryObjectives}><b>Objectives:</b> {lesson.objectives.join(' · ')}</p>}
          {lesson.competencies?.length>0&&<div style={s.competencyList}>{lesson.competencies.map(item=><span key={item.name}>{item.name}{item.score==null?'':` · ${item.score}%`}</span>)}</div>}
          {!learnerId&&<div style={s.lessonProgressActions}><button style={s.secondary} disabled={savingLesson===lesson.id} onClick={()=>saveProgress(lesson,'in_progress',Math.max(lesson.progress_pct||0,25))}>Start / Continue</button><button style={s.primary} disabled={savingLesson===lesson.id} onClick={()=>saveProgress(lesson,'completed',100)}><CheckCircle2 size={14}/>Mark complete</button></div>}
          {lesson.assessment_evidence?.length>0&&<details style={s.sources}><summary>Assessment evidence ({lesson.assessment_evidence.length})</summary>{lesson.assessment_evidence.map(item=><div key={item.artifact_id}>{item.artifact_title}: {item.correct_count} / {item.question_count}</div>)}</details>}
        </article>)}</div>
      </Section>)}
      {!mastery.modules?.length&&<Empty title="No curriculum yet" text="Add modules and lessons before tracking learner progress and mastery."/>}
    </>}
  </div>;
}

function Metric({label,value,detail}) { return <article style={s.metric}><small>{label}</small><strong>{value}</strong><span>{detail}</span></article> }

function Questions({course,mutate}) {
  const [form,setForm]=useState({target_role:'teacher',question:''});
  const submit=async()=>{await mutate(()=>createLearningQuestion(course.id,{...form,context:{course_title:course.title,source_document_ids:(course.assets||[]).map(x=>x.document_id)}}));setForm({...form,question:''})};
  return <div style={s.scroll}><Section title="Ask a person" icon={<CircleHelp size={16}/>}><div style={s.questionForm}><select value={form.target_role} onChange={e=>setForm({...form,target_role:e.target.value})}><option value="teacher">Teacher</option><option value="advisor">Advisor</option></select><textarea placeholder="What needs human guidance or clarification?" value={form.question} onChange={e=>setForm({...form,question:e.target.value})}/><button style={s.primary} disabled={!form.question.trim()} onClick={submit}><Send size={15}/>Send</button></div></Section>
  <div style={s.cards}>{(course.questions||[]).map(item=><QuestionCard key={item.id} item={item} course={course} mutate={mutate}/>)}</div>{!course.questions?.length&&<Empty title="No escalations" text="Questions that require judgment can be routed to a teacher or advisor with course context."/>}</div>;
}

function QuestionCard({item,course,mutate}) { const [answer,setAnswer]=useState(item.answer||'');const canAnswer=['teacher','advisor','admin'].includes(course.my_persona)||course.can_manage;return <article style={s.question}><header style={s.questionHeader}><span style={s.status}>{item.status}</span><small>To {item.target_role} · {item.asker_name||item.asker_email}</small></header><strong>{item.question}</strong>{(canAnswer||item.answer)&&<textarea disabled={!canAnswer||item.status==='closed'} value={answer} placeholder="Write a reviewed response..." onChange={e=>setAnswer(e.target.value)}/>}<div style={s.inlineActions}>{canAnswer&&item.status!=='closed'&&<button style={s.primary} disabled={!answer.trim()} onClick={()=>mutate(()=>updateLearningQuestion(course.id,item.id,{answer,status:'answered'}))}><Save size={14}/>Answer</button>}{item.status==='answered'&&<button style={s.secondary} onClick={()=>mutate(()=>updateLearningQuestion(course.id,item.id,{status:'closed'}))}>Close</button>}</div></article>}

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
  addRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,190px),1fr))',gap:7,alignItems:'end'},checkRow:{display:'flex',alignItems:'center',gap:16,minHeight:36,flexWrap:'wrap'},cards:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,280px),1fr))',gap:8},card:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:9,border:'1px solid var(--b1)',borderRadius:6,background:'var(--s2)'},metaStack:{display:'flex',flexDirection:'column',gap:4,minWidth:0,lineHeight:1.35},lesson:{display:'grid',gridTemplateColumns:'30px minmax(0,1fr) 30px',gap:8,alignItems:'center',marginBottom:9,padding:'8px 0',borderBottom:'1px solid var(--b1)'},lessonNumber:{alignSelf:'start',display:'grid',placeItems:'center',width:25,height:25,borderRadius:5,background:'rgba(74,222,128,.1)',color:'#86efac',fontSize:11,fontWeight:800},lessonFields:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,220px),1fr))',gap:8,minWidth:0},inlineActions:{display:'flex',justifyContent:'flex-end',gap:6,marginTop:7},toolbar:{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap',marginBottom:10},mappingRow:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,170px),1fr))',gap:8,alignItems:'end'},
  rangeHint:{marginTop:8,color:'#86efac',fontSize:10.5},mappingFeedback:{marginTop:8,padding:'6px 8px',border:'1px solid rgba(250,204,21,.3)',borderRadius:5,background:'rgba(250,204,21,.08)',color:'#fde68a',fontSize:10.5},assetGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,430px),1fr))',gap:8},asset:{display:'grid',gridTemplateColumns:'38px minmax(0,1fr) 30px',gap:9,alignItems:'center',padding:10,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},assetIcon:{display:'grid',placeItems:'center',width:36,height:36,borderRadius:6,background:'rgba(74,222,128,.1)',color:'#4ade80'},assetMapping:{minWidth:0,padding:10,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},assetHeader:{display:'flex',alignItems:'center',gap:9,minWidth:0},assetScope:{margin:'8px 0',padding:'6px 8px',borderRadius:5,background:'rgba(74,222,128,.06)',color:'var(--muted2)',fontSize:10.5},assetMappingControls:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,170px),1fr))',gap:7,alignItems:'end'},ellipsis:{display:'block',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'},scopeBar:{display:'flex',alignItems:'end',justifyContent:'space-between',gap:10,padding:'8px 12px',borderBottom:'1px solid var(--b1)',background:'var(--s1)',flexWrap:'wrap'},scopeSelectors:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,180px),1fr))',gap:7,flex:'1 1 390px',maxWidth:520},studyTarget:{display:'flex',alignItems:'center',gap:7,margin:'8px 0',padding:'7px 9px',border:'1px solid rgba(74,222,128,.2)',borderRadius:6,background:'rgba(74,222,128,.06)',color:'var(--muted2)',fontSize:11},
  tutor:{height:'100%',display:'flex',flexDirection:'column'},messages:{flex:1,minHeight:0,overflowY:'auto',padding:12},message:{maxWidth:'min(820px,92%)',marginBottom:9,padding:'9px 11px',borderRadius:7},userMessage:{marginLeft:'auto',background:'#166534',color:'#fff'},aiMessage:{marginRight:'auto',border:'1px solid var(--b1)',background:'var(--s1)'},messageText:{marginTop:5,whiteSpace:'pre-wrap',lineHeight:1.55,fontSize:13},tutorAnswer:{marginTop:5,fontSize:13,lineHeight:1.65,color:'var(--tx2)',overflowWrap:'anywhere'},evidenceBlock:{marginTop:9,borderTop:'1px solid var(--b1)'},boundary:{display:'flex',alignItems:'center',gap:5,padding:'7px 0',color:'#86efac',fontSize:10.5},sources:{marginTop:0,paddingTop:5,fontSize:10.5,color:'var(--muted2)'},evidenceGrid:{display:'grid',gap:6,marginTop:7},evidenceCard:{padding:8,border:'1px solid var(--b1)',borderRadius:6,background:'var(--s2)'},evidenceCardHeader:{display:'flex',justifyContent:'space-between'},noEvidence:{display:'flex',alignItems:'center',gap:5,color:'#fde68a',fontSize:10.5},composer:{display:'grid',gridTemplateColumns:'minmax(78px,108px) minmax(0,1fr) 40px 40px',gap:7,padding:10,borderTop:'1px solid var(--b1)',background:'var(--s1)'},tutorLanguage:{minWidth:0,width:'100%',padding:'0 7px',border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'var(--tx)',fontSize:11},voiceButton:{display:'grid',placeItems:'center',padding:0,border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'#86efac',cursor:'pointer'},voiceButtonOn:{borderColor:'rgba(248,113,113,.55)',background:'rgba(248,113,113,.12)',color:'#fca5a5'},voiceStatus:{gridColumn:'1/-1',fontSize:10.5,color:'var(--muted2)'},send:{display:'grid',placeItems:'center',border:0,borderRadius:6,background:'#15803d',color:'#fff',cursor:'pointer'},
  artifactGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,360px),1fr))',gap:9},artifact:{padding:11,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},artifactHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8},quizArtifact:{gridColumn:'1/-1'},flashcardDeck:{display:'flex',flexDirection:'column',gap:12,marginTop:10},flashcardExchange:{display:'flex',flexDirection:'column',gap:7,padding:'10px 0',borderTop:'1px solid var(--b1)'},flashcardLabel:{alignSelf:'center',padding:'3px 8px',borderRadius:12,background:'rgba(74,222,128,.08)',color:'var(--muted2)',fontSize:10},flashcardMessage:{width:'min(820px,92%)',boxSizing:'border-box',marginBottom:0},quiz:{display:'flex',flexDirection:'column',gap:12,marginTop:10},quizSummary:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,flexWrap:'wrap',padding:10,border:'1px solid rgba(74,222,128,.22)',borderRadius:6,background:'rgba(74,222,128,.06)'},quizScore:{display:'flex',gap:6,flexWrap:'wrap',color:'#bbf7d0',fontSize:11},quizQuestion:{padding:12,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s2)'},quizQuestionHeader:{display:'flex',alignItems:'flex-start',gap:8,marginBottom:10},questionNumber:{display:'grid',placeItems:'center',width:25,height:25,flexShrink:0,borderRadius:5,background:'rgba(74,222,128,.12)',color:'#86efac',fontWeight:800},correctBadge:{display:'inline-flex',alignItems:'center',gap:4,marginLeft:'auto',padding:'3px 7px',borderRadius:12,background:'rgba(74,222,128,.12)',color:'#86efac',fontSize:10},incorrectBadge:{display:'inline-flex',alignItems:'center',gap:4,marginLeft:'auto',padding:'3px 7px',borderRadius:12,background:'rgba(248,113,113,.12)',color:'#fca5a5',fontSize:10},quizOptions:{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:7,marginBottom:10},quizOption:{display:'grid',gridTemplateColumns:'20px 24px minmax(0,1fr) 18px',alignItems:'center',gap:7,minHeight:48,padding:'8px 10px',border:'1px solid var(--b1)',borderRadius:6,background:'var(--s1)',color:'var(--tx2)',cursor:'pointer'},correctOption:{borderColor:'rgba(74,222,128,.55)',background:'rgba(74,222,128,.1)',color:'#dcfce7'},incorrectOption:{borderColor:'rgba(248,113,113,.55)',background:'rgba(248,113,113,.1)',color:'#fee2e2'},answerFeedback:{display:'flex',flexDirection:'column',gap:5,padding:10,borderRadius:6,lineHeight:1.45},correctFeedback:{border:'1px solid rgba(74,222,128,.35)',background:'rgba(74,222,128,.08)'},incorrectFeedback:{border:'1px solid rgba(248,113,113,.35)',background:'rgba(248,113,113,.08)'},clamped:{display:'-webkit-box',WebkitLineClamp:8,WebkitBoxOrient:'vertical',overflow:'hidden',marginTop:8,whiteSpace:'pre-wrap',fontSize:12,lineHeight:1.5,color:'var(--tx2)'},linkBtn:{marginTop:7,padding:0,border:0,background:'transparent',color:'#4ade80',cursor:'pointer'},questionForm:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,220px),1fr))',gap:7,alignItems:'end'},question:{display:'flex',flexDirection:'column',gap:9,padding:11,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},questionHeader:{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'},status:{padding:'3px 7px',borderRadius:12,background:'rgba(74,222,128,.1)',color:'#86efac',fontSize:10,textTransform:'uppercase'},
  progressToolbar:{display:'flex',alignItems:'end',justifyContent:'space-between',gap:12,marginBottom:10,flexWrap:'wrap'},progressTitle:{display:'flex',alignItems:'center',gap:7,margin:'0 0 4px',fontSize:15},progressActions:{display:'flex',alignItems:'end',gap:7,flexWrap:'wrap'},metricGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,190px),1fr))',gap:8,marginBottom:12},metric:{display:'flex',flexDirection:'column',gap:5,padding:12,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},recommendationGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,280px),1fr))',gap:8},recommendation:{display:'grid',gridTemplateColumns:'28px minmax(0,1fr)',gap:8,padding:10,border:'1px solid rgba(74,222,128,.18)',borderRadius:6,background:'var(--s2)'},recommendationNumber:{display:'grid',placeItems:'center',width:25,height:25,borderRadius:5,background:'rgba(74,222,128,.12)',color:'#86efac',fontWeight:800},moduleSummary:{display:'flex',gap:8,flexWrap:'wrap',marginBottom:9},masteryLessons:{display:'grid',gap:8},masteryLesson:{padding:10,border:'1px solid var(--b1)',borderRadius:6,background:'var(--s2)'},masteryLessonHeader:{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:8,flexWrap:'wrap'},masteryBadge:{padding:'4px 7px',borderRadius:12,background:'rgba(148,163,184,.1)',color:'var(--muted2)',fontSize:10},masteredBadge:{background:'rgba(74,222,128,.12)',color:'#86efac'},developingBadge:{background:'rgba(250,204,21,.12)',color:'#fde68a'},masteryObjectives:{margin:'8px 0',fontSize:11,color:'var(--muted2)'},competencyList:{display:'flex',gap:5,flexWrap:'wrap'},lessonProgressActions:{display:'flex',justifyContent:'flex-end',gap:6,marginTop:9,flexWrap:'wrap'},
  assignmentGrid:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,420px),1fr))',gap:10},assignment:{padding:12,border:'1px solid var(--b1)',borderRadius:7,background:'var(--s1)'},assignmentHeader:{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:8},rubricList:{display:'grid',gap:7},rubricRow:{display:'grid',gridTemplateColumns:'minmax(130px,.4fr) minmax(180px,1fr) 76px 30px',gap:7},submissionBox:{display:'grid',gap:9,marginTop:10,paddingTop:10,borderTop:'1px solid var(--b1)'},documentChecks:{display:'grid',maxHeight:130,overflowY:'auto',gap:5,padding:7,border:'1px solid var(--b1)',borderRadius:6},uploadButton:{display:'inline-flex',alignItems:'center',justifyContent:'center',gap:6,minHeight:34,padding:'7px 10px',border:'1px solid var(--b2)',borderRadius:6,background:'var(--s2)',color:'var(--tx)',cursor:'pointer'},feedback:{padding:8,borderLeft:'3px solid #4ade80',background:'rgba(74,222,128,.06)',whiteSpace:'pre-wrap'},reviewList:{display:'grid',gap:8,marginTop:10},reviewCard:{display:'grid',gap:8,padding:9,border:'1px solid var(--b1)',borderRadius:6,background:'var(--s2)'},aiEvaluation:{display:'grid',gap:8,padding:10,border:'1px solid rgba(74,222,128,.28)',borderRadius:6,background:'rgba(74,222,128,.05)',color:'var(--tx2)'},evaluationHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,flexWrap:'wrap'},evaluationScore:{color:'#86efac',fontSize:14},evaluationCriteria:{display:'grid',gap:7},evaluationCriterion:{display:'grid',gap:5,padding:8,border:'1px solid var(--b1)',borderRadius:5,background:'var(--s1)'},evaluationEvidence:{display:'block',padding:6,borderLeft:'2px solid #60a5fa',color:'var(--tx2)',whiteSpace:'pre-wrap'},evaluationDetails:{paddingTop:3},evaluationDiagnostic:{padding:8,border:'1px solid rgba(251,191,36,.32)',borderRadius:5,background:'rgba(251,191,36,.06)',overflowWrap:'anywhere'},scoreInput:{width:90},dashboardColumns:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,330px),1fr))',gap:10},dashboardTable:{display:'grid',gap:5,overflowX:'auto'},dashboardRow:{display:'grid',gridTemplateColumns:'minmax(150px,1.2fr) repeat(2,minmax(90px,.6fr)) minmax(180px,1fr)',gap:8,alignItems:'center',padding:8,borderBottom:'1px solid var(--b1)',fontSize:11,minWidth:620},riskText:{color:'#fca5a5'},goodText:{color:'#86efac'},insightList:{display:'grid',gap:6},
  empty:{height:'100%',minHeight:150,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:7,padding:18,textAlign:'center',color:'var(--muted2)'},markdownPreview:{maxHeight:360,marginTop:8,overflow:'auto',padding:'2px 8px 2px 2px',borderTop:'1px solid var(--b1)'},artifactMarkdown:{fontSize:13,lineHeight:1.65,color:'var(--tx2)'},readerOverlay:{position:'fixed',inset:0,zIndex:3300,display:'grid',placeItems:'center',padding:'max(12px,env(safe-area-inset-top)) max(12px,env(safe-area-inset-right)) max(12px,env(safe-area-inset-bottom)) max(12px,env(safe-area-inset-left))',background:'rgba(0,0,0,.8)'},reader:{width:'min(900px,100%)',height:'min(86dvh,760px)',display:'flex',flexDirection:'column',overflow:'hidden',border:'1px solid var(--b2)',borderRadius:8,background:'#102010'},readerHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:10,borderBottom:'1px solid var(--b1)',background:'#142714',flexShrink:0},markdownReader:{flex:1,minHeight:0,overflow:'auto',padding:16},readerMarkdown:{fontSize:14,lineHeight:1.75,color:'var(--tx)'},
  editorField:{position:'relative',minWidth:0,width:'100%'},scrollableTextarea:{display:'block',width:'100%',height:86,minHeight:64,maxHeight:180,overflowY:'auto',resize:'vertical',padding:'9px 36px 9px 10px',boxSizing:'border-box',lineHeight:1.45},compactTextarea:{height:58,minHeight:48,maxHeight:130},expandBtn:{position:'absolute',top:6,right:6,display:'grid',placeItems:'center',width:27,height:27,padding:0,border:'1px solid var(--b2)',borderRadius:5,background:'var(--s2)',color:'#86efac',cursor:'pointer'},editorDialog:{width:'min(980px,100%)',height:'min(88dvh,820px)',display:'flex',flexDirection:'column',overflow:'hidden',border:'1px solid rgba(74,222,128,.3)',borderRadius:8,background:'#102010',boxShadow:'0 24px 80px rgba(0,0,0,.65)'},editorHeader:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8,padding:'10px 12px',borderBottom:'1px solid var(--b1)',background:'#142714',flexShrink:0},largeEditor:{flex:1,minHeight:0,width:'100%',overflow:'auto',resize:'none',padding:16,boxSizing:'border-box',border:0,borderRadius:0,background:'var(--s1)',color:'var(--tx)',fontFamily:'inherit',fontSize:15,lineHeight:1.6},editorFooter:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,padding:'9px 12px',borderTop:'1px solid var(--b1)',background:'#142714',color:'var(--muted2)',fontSize:10.5,flexShrink:0},
};
