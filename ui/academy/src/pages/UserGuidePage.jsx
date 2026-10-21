// src/pages/UserGuidePage.jsx -- in-app reference for the three personas
// (Student / Teacher / Admin). Defaults to the viewer's own role (passed
// in as defaultRole by the caller -- App.jsx works this out from
// workspace.my_role for the signed-in app, and the guest preview always
// passes 'student') but lets anyone switch tabs to read the others, since
// this is just documentation, not a permission boundary.
import React, { useState } from 'react';

const GUIDE = {
  student: {
    label: 'Student',
    icon: '🎓',
    intro: "Everything below is what you'll see once you're enrolled in a course. If you're trying the Guest login preview, the same tools are here -- just capped to a limited number of questions/generations per session.",
    sections: [
      {
        title: 'Finding your way around',
        text: "Courses lists everything you're enrolled in. Open one to get its own Course page, grouped into two dropdowns at the top so everything fits on a single row even on a phone: Course (Overview, Curriculum, Calendar) and Learning Tools (AI Tutor, Study Tools, Assignments, Q&A, Progress & Mastery).",
      },
      {
        title: 'Why you only see some courses',
        text: 'Every course lives inside a workspace -- usually one per semester or cohort (e.g. "Fall 2026"), set up by an admin. That\'s why your Courses list only shows the term or cohort you\'re actually enrolled in, not every course the institution has ever run.',
      },
      {
        title: 'Curriculum',
        text: "Browse modules and lessons in the left-hand list (on mobile, tap ☰ to open it, then it collapses again once you pick something so the content gets the full screen). Each module/lesson can have documents, audio, or video attached -- open a document to read it (a .doc/.docx is converted to a real PDF automatically), or play a video right in the browser. Every module and lesson has its own discussion thread underneath its content, so you can ask classmates or your teacher a question right where the material is.",
      },
      {
        title: 'AI Tutor',
        text: "Ask anything about the course and get an answer grounded in your own course material, with citations back to the source -- never a generic answer. You can type or use the 🎙 microphone button, and have any answer read aloud with 🔊 Listen (pressing it again stops it, and starting a new one automatically stops whatever was already playing, so you never get two answers talking over each other). Pick a reply language from the dropdown if you'd rather think in a language other than the course's own.",
      },
      {
        title: 'Study Tools',
        text: "Generate a summary, a full study guide, a key-concepts breakdown, flashcards, or a practice quiz, scoped to the whole course, a single module, or a single lesson, and in whichever language you choose. On mobile this is laid out as Course detail + Study Tools type on one line and Language + Generate on the next, so nothing feels cramped. Anything you generate can be saved to the course, and a saved practice quiz becomes a real, gradeable quiz you can answer and submit.",
      },
      {
        title: 'Assignments & Projects',
        text: 'Submit your work for each assignment your teacher publishes. Submissions get fast AI-assisted grading first, but a real instructor always reviews it before a grade is final -- you\'re never graded by AI alone. Each assignment has its own discussion thread for questions about the brief.',
      },
      {
        title: 'Q&A',
        text: "For anything that genuinely needs a teacher's judgment rather than an AI answer, post it here. Your teacher (or an admin) will respond, and you can track the status of your question.",
      },
      {
        title: 'Progress & Mastery',
        text: "See your own progress lesson by lesson and your mastery level concept by concept, backed by evidence from what you've actually done in the course -- not a guess.",
      },
      {
        title: 'Calendar',
        text: 'Announcements and deadlines your teacher has posted for the course, in one place.',
      },
      {
        title: 'Content Library',
        text: "Every document, audio file, and video uploaded to the workspace lives here. Select one or more items and chat with them by voice or text -- ask follow-up questions, get instant summaries in any language, and view the original (again, .doc/.docx opens as a converted PDF).",
      },
      {
        title: 'Video Intelligence',
        text: 'Play any processed video, ask questions about what happens in it (answered from its own transcript/timeline), and summarize it in whichever language you like -- all from the same screen, with the video player and the answer area both getting proper room to breathe.',
      },
    ],
  },
  teacher: {
    label: 'Teacher',
    icon: '🧑‍🏫',
    intro: 'Everything a student has, plus the tools to build and run a course.',
    sections: [
      {
        title: 'Building a course',
        text: "Curriculum authoring lets you add modules and lessons, write descriptions and summaries, and attach content from the workspace's Content Library (or Video Intelligence, for video) to the right module or lesson -- course-wide, or scoped to one specific spot. A \"Student view\" toggle shows you exactly what students will see before you publish.",
      },
      {
        title: 'Workspaces, semesters, and cohorts',
        text: 'Every course you build lives inside a workspace, and institutions typically set up one workspace per semester or cohort for a clean break between terms -- a student only ever sees courses in the workspace(s) they belong to. Setting that up (creating a new workspace, inviting members into it) is an admin task, but it\'s worth knowing: if you\'re asked to "move this course to Spring 2027," that usually means recreating or re-attaching it inside a new workspace your admin creates for that term, not just editing the course\'s own Semester field (which is just a label shown inside the course itself).',
      },
      {
        title: 'Content Library',
        text: 'Upload documents and audio here; they\'re classified, chunked, and embedded automatically so the AI Tutor and Study Tools can use them. You can view the original, inspect the chunks a document was split into, trigger or re-trigger embedding, reclassify, retry a failed document, delete, or compare two documents side by side.',
      },
      {
        title: 'Video Intelligence',
        text: "Upload video separately from documents (it goes straight to storage rather than through the regular upload path, since video files are large) and choose a processing profile -- standard, or cultural performance for music/dance-style content -- before it's processed into a searchable, summarizable, question-answerable asset.",
      },
      {
        title: 'Assignments & Projects',
        text: 'Create assignments and projects, and review every submission\'s AI-assisted grade before it becomes final -- you always have the last word, and can adjust the grade or feedback. Each assignment\'s own discussion thread is a good place to post clarifications.',
      },
      {
        title: 'Discussion moderation',
        text: 'Module, lesson, and assignment discussion threads are yours to participate in and moderate -- remove a post if it needs removing.',
      },
      {
        title: 'Q&A',
        text: "Student questions that need a teacher's judgment land here. Answer them directly, or escalate further if an admin should weigh in.",
      },
      {
        title: 'Progress & Mastery',
        text: "See the whole course's progress and mastery, not just one student's -- this is how you spot who's struggling with a specific concept early, before it shows up as a missed assignment.",
      },
      {
        title: 'Calendar',
        text: 'Post announcements and deadlines for the whole course.',
      },
      {
        title: 'Directory',
        text: 'Your course roster. Add members one at a time or in bulk, remove them, and update their profile details.',
      },
      {
        title: 'Instructor Intelligence',
        text: "A course-level analytics dashboard -- a higher vantage point than Progress & Mastery's per-student view, useful for spotting patterns across the whole class.",
      },
    ],
  },
  admin: {
    label: 'Admin',
    icon: '🏛️',
    intro: 'Everything a Teacher has, plus running the workspace itself -- the institution or department this all belongs to.',
    sections: [
      {
        title: 'Workspace Admin',
        text: 'Manage who belongs to the workspace and what they can do: owner (full administrative control), editor (can author courses and manage content -- the Teacher role), or viewer (read-only -- the Student role, with upload/delete controls hidden and blocked server-side either way). Invite members, change roles, and remove access here.',
      },
      {
        title: 'Organizing by semester or cohort',
        text: 'A workspace is a fully isolated container -- its own members, roles, courses, and Content Library, invisible to every other workspace. Use that for logical grouping: create one workspace per semester or cohort (e.g. "Fall 2026" or "Cohort A -- Fall 2026") via "+ Create your workspace" / "+ New workspace" in the workspace switcher at the top of the app, so each term or cohort’s membership and content stay cleanly separated from the next. Within a single workspace, a course’s own Semester field (set from its Overview tab) is the finer-grained label for when a workspace intentionally spans more than one term.',
      },
      {
        title: 'Everything a Teacher can do',
        text: 'Course authoring, Content Library, Video Intelligence, Assignments, discussion moderation, Q&A, Progress & Mastery, Calendar, and Directory are all available to you too, across every course in the workspace -- not just ones you personally built.',
      },
      {
        title: 'The Guest login preview',
        text: "Prospective students (or an institution evaluating the platform) can try the student experience with no sign-up at all through the Guest login -- it's a capped, read-only preview of the exact same student tools (AI Tutor, Study Tools, Content Library, Video Intelligence, Discussion, Q&A, Progress, Calendar). It's a good thing to point people at before asking them to create an account. Signing up as Teacher, Admin, or Student unlocks the full, uncapped experience.",
      },
    ],
  },
};

export default function UserGuidePage({ defaultRole = 'student' }) {
  const [role, setRole] = useState(GUIDE[defaultRole] ? defaultRole : 'student');
  const guide = GUIDE[role];

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <h2 style={s.h2}>📖 User Guide</h2>
        <p style={s.subtitle}>How to get the most out of ADAR Knowledge Academy, role by role.</p>
      </div>
      <div style={s.roleTabs}>
        {Object.entries(GUIDE).map(([key, g]) => (
          <button key={key} type="button" style={{ ...s.roleTab, ...(role === key ? s.roleTabActive : {}) }} onClick={() => setRole(key)}>
            {g.icon} {g.label}
          </button>
        ))}
      </div>
      <p style={s.intro}>{guide.intro}</p>
      <div style={s.sections}>
        {guide.sections.map((sec, i) => (
          <section key={i} style={s.section}>
            <strong style={s.sectionTitle}>{sec.title}</strong>
            <p style={s.sectionText}>{sec.text}</p>
          </section>
        ))}
      </div>
    </div>
  );
}

const s = {
  wrap: { padding: '20px 22px', maxWidth: 820, margin: '0 auto', width: '100%' },
  header: { marginBottom: 14 },
  h2: { fontSize: 19, fontWeight: 700, color: 'var(--tx)', marginBottom: 4 },
  subtitle: { fontSize: 12.5, color: 'var(--muted2)' },
  roleTabs: { display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' },
  roleTab: { padding: '8px 14px', borderRadius: 8, border: '1px solid var(--b2)', background: 'var(--s2)', color: 'var(--tx2)', fontSize: 12.5, fontWeight: 700, whiteSpace: 'nowrap' },
  roleTabActive: { background: 'var(--gold-soft)', color: 'var(--gold-h)', borderColor: 'var(--gold-soft)' },
  intro: { fontSize: 12.5, color: 'var(--muted2)', lineHeight: 1.6, marginBottom: 18, paddingBottom: 14, borderBottom: '1px solid var(--b1)' },
  sections: { display: 'grid', gap: 16 },
  section: { background: 'var(--s1)', border: '1px solid var(--b1)', borderRadius: 10, padding: '14px 16px' },
  sectionTitle: { fontSize: 13.5, color: 'var(--tx)', display: 'block', marginBottom: 6 },
  sectionText: { fontSize: 12.5, color: 'var(--tx2)', lineHeight: 1.65 },
};
