/* ─── State ─────────────────────────────────────────────────────── */
const state = {
  threads: [],
  folders: [],
  currentConversationId: null,
  currentThread: null,
  toRecipients: [],
  ccRecipients: [],
  bccRecipients: [],
  originalRecipients: { to: [], cc: [], bcc: [] },
  autocompleteTimers: {},
  isSyncing: false,
  currentUser: null,
  activeProjectId: sessionStorage.getItem('active_project_id') || null,
  activeProjectName: sessionStorage.getItem('active_project_name') || null,
  activeTab: 'mail',
};

const STATUS_COLORS = {
  inbox: '#3b82f6',
  auto_reply: '#22c55e',
  manual: '#f59e0b',
  done: '#6b7280',
};

/* ─── Init ──────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  await checkAuth();
  initSortable();
  connectWebSocket();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.currentConversationId) {
    closePanel();
  }
});

function onPanelBackdropClick(e) {
  if (e.target === document.getElementById('detail-panel')) {
    closePanel();
  }
}

/* ─── Auth ──────────────────────────────────────────────────────── */
function getCsrfToken() {
  return localStorage.getItem('csrf_token') || '';
}

function csrfHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-CSRF-Token': getCsrfToken(),
  };
}

async function checkAuth() {
  try {
    const res = await fetch('/api/auth/me', { credentials: 'include' });
    if (res.status === 401) {
      window.location.href = '/login';
      return;
    }
    if (!res.ok) {
      showToast('인증 확인 중 오류가 발생했습니다.', 'error');
      return;
    }
    const data = await res.json();
    state.currentUser = data;
    await loadProfile(data);
    await checkOutlookStatus();
    await loadFolders();
    await loadAllThreads();
  } catch (e) {
    window.location.href = '/login';
  }
}

async function loadProfile(data) {
  if (!data) {
    try {
      const res = await fetch('/api/auth/me', { credentials: 'include' });
      if (!res.ok) return;
      data = await res.json();
    } catch (e) { return; }
  }

  document.getElementById('profile-area').style.display = 'flex';
  document.getElementById('header-nav').style.display = 'flex';

  document.getElementById('profile-name').textContent = data.display_name || '-';
  document.getElementById('profile-email').textContent = data.email || '-';

  const avatar = document.getElementById('profile-avatar');
  const initials = (data.display_name || '?').split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
  avatar.textContent = initials;

  // 관리자 링크 표시
  if (data.role === 'admin') {
    document.getElementById('admin-link').style.display = 'inline';
  }

  // 프로젝트 선택기 표시
  const selector = document.getElementById('project-selector');
  const label = document.getElementById('project-name-label');
  if (selector && label) {
    selector.style.display = 'flex';
    label.textContent = state.activeProjectName || '전체 보기';
  }
}

async function checkOutlookStatus() {
  try {
    const res = await fetch('/api/outlook/status', { credentials: 'include' });
    if (!res.ok) return;
    const data = await res.json();
    const banner = document.getElementById('outlook-banner');
    if (banner) banner.style.display = data.connected ? 'none' : 'flex';
  } catch (e) {}
}

async function doLogout() {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'include',
    });
    localStorage.removeItem('csrf_token');
    window.location.href = '/login';
  } catch (e) {
    window.location.href = '/login';
  }
}

/* ─── Folders ───────────────────────────────────────────────────── */
async function loadFolders() {
  try {
    const res = await fetch('/api/folders', { credentials: 'include' });
    if (!res.ok) {
      console.error('폴더 로드 실패:', res.status);
      const tree = document.getElementById('folder-tree');
      if (tree) tree.innerHTML = '<div class="empty-state"><div>폴더를 불러올 수 없습니다.</div></div>';
      return;
    }
    const data = await res.json();
    state.folders = data.folders || [];
    renderFolderTree();
  } catch (e) {
    console.error('폴더 로드 실패:', e);
  }
}

async function refreshFolders() {
  showToast('폴더 목록 새로고침 중...', 'info');
  try {
    const res = await fetch('/api/folders/refresh', { method: 'POST', headers: csrfHeaders(), credentials: 'include' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 503) {
        showToast('Outlook이 연동되지 않았습니다. 설정에서 연동해주세요.', 'error');
      } else {
        showToast(err.detail || '폴더 새로고침 실패', 'error');
      }
      return;
    }
    await loadFolders();
    showToast('폴더 목록이 업데이트되었습니다', 'success');
  } catch (e) {
    showToast('폴더 새로고침 실패', 'error');
  }
}

function renderFolderTree() {
  const tree = document.getElementById('folder-tree');
  if (!state.folders.length) {
    tree.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📁</div><div>폴더가 없습니다</div></div>';
    return;
  }

  const rootFolders = state.folders.filter(f => !f.parent_id);
  const childMap = {};
  state.folders.filter(f => f.parent_id).forEach(f => {
    if (!childMap[f.parent_id]) childMap[f.parent_id] = [];
    childMap[f.parent_id].push(f);
  });

  tree.innerHTML = rootFolders.map(f => renderFolderItem(f, childMap, 0)).join('');
}

function renderFolderItem(folder, childMap, depth) {
  const children = childMap[folder.folder_id] || [];
  const isChecked = folder.is_synced ? 'checked' : '';
  const paddingLeft = depth * 12;

  const childrenHtml = children.length
    ? `<div class="folder-children">${children.map(c => renderFolderItem(c, childMap, depth + 1)).join('')}</div>`
    : '';

  return `
    <div>
      <div class="folder-item" style="padding-left: ${12 + paddingLeft}px">
        <input type="checkbox" ${isChecked}
               onchange="toggleFolder('${folder.folder_id}', this.checked)"
               id="folder-${folder.folder_id}">
        <span class="folder-name" title="${folder.folder_name}">${folder.folder_name}</span>
        ${folder.mail_count > 0 ? `<span class="folder-count">${folder.mail_count}</span>` : ''}
      </div>
      ${childrenHtml}
    </div>
  `;
}

function toggleFolder(folderId, isChecked) {
  const folder = state.folders.find(f => f.folder_id === folderId);
  if (folder) folder.is_synced = isChecked;
}

function selectAllFolders() {
  state.folders.forEach(f => f.is_synced = true);
  renderFolderTree();
}

function deselectAllFolders() {
  state.folders.forEach(f => f.is_synced = false);
  renderFolderTree();
}

async function saveFolderSelection() {
  const selectedIds = state.folders.filter(f => f.is_synced).map(f => f.folder_id);
  if (!selectedIds.length) {
    showToast('동기화할 폴더를 하나 이상 선택해주세요', 'error');
    return;
  }

  showToast(`${selectedIds.length}개 폴더 설정 저장 중...`, 'info');
  try {
    const saveRes = await fetch('/api/folders/save-selection', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ selected_folder_ids: selectedIds }),
      credentials: 'include',
    });
    if (!saveRes.ok) {
      const err = await saveRes.json().catch(() => ({}));
      showToast(err.detail || '폴더 설정 저장 실패', 'error');
      return;
    }

    showToast('설정 저장 완료! 전체 동기화를 시작합니다...', 'success');

    const syncRes = await fetch('/api/emails/full-sync', { method: 'POST', headers: csrfHeaders(), credentials: 'include' });
    if (!syncRes.ok) {
      const err = await syncRes.json().catch(() => ({}));
      if (syncRes.status === 503) {
        showToast('Outlook이 연동되지 않았습니다. 설정에서 연동해주세요.', 'error');
      } else {
        showToast(err.detail || '동기화 시작 실패', 'error');
      }
      return;
    }

    showToast('동기화가 시작되었습니다. 잠시 후 메일이 표시됩니다.', 'info');
    setTimeout(async () => {
      await loadAllThreads();
    }, 3000);
  } catch (e) {
    showToast('저장 실패: ' + e.message, 'error');
  }
}

/* ─── Threads ───────────────────────────────────────────────────── */
async function loadAllThreads() {
  try {
    let url = '/api/threads';
    if (state.activeProjectId) {
      url += `?project_id=${encodeURIComponent(state.activeProjectId)}`;
    }
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) {
      console.error('쓰레드 로드 실패:', res.status);
      const statuses = ['inbox', 'auto_reply', 'manual', 'done'];
      statuses.forEach(s => {
        const list = document.getElementById(`list-${s}`);
        if (list) list.innerHTML = '<div class="empty-state"><div>데이터를 불러올 수 없습니다.</div></div>';
      });
      return;
    }
    const data = await res.json();
    state.threads = data.threads || [];
    renderKanban();
    updateSyncTime();
  } catch (e) {
    console.error('쓰레드 로드 실패:', e);
  }
}

function renderKanban() {
  const statuses = ['inbox', 'auto_reply', 'manual', 'done'];
  statuses.forEach(status => {
    const list = document.getElementById(`list-${status}`);
    const count = document.getElementById(`count-${status}`);
    const threads = state.threads.filter(t => t.status === status);

    count.textContent = threads.length;

    if (!threads.length) {
      list.innerHTML = `<div class="empty-state">
        <div class="empty-state-icon">${status === 'done' ? '✅' : '📭'}</div>
        <div>없음</div>
      </div>`;
      return;
    }

    list.innerHTML = threads.map(t => renderCard(t)).join('');

    list.querySelectorAll('.kanban-card').forEach(card => {
      card.addEventListener('click', () => openPanel(card.dataset.id));
    });
  });
}

function renderCard(thread) {
  const color = STATUS_COLORS[thread.status] || '#6b7280';
  const time = formatTime(thread.latest_at);
  const subject = thread.subject || thread.body_preview || '(제목 없음)';

  const badges = [];
  if (thread.has_attachments) {
    badges.push(`<span class="badge badge-attachment">📎</span>`);
  }
  if (thread.has_folder_mismatch) {
    badges.push(`<span class="badge badge-mismatch">⚠️</span>`);
  }
  if (thread.has_new_reply) {
    badges.push(`<span class="badge badge-new-reply">NEW</span>`);
  }

  // 클레임 배지
  let claimBadge = '';
  if (thread.claimed_by) {
    const isMine = thread.claimed_by === (state.currentUser && state.currentUser.user_id);
    const name = thread.claimed_by_name || thread.claimed_by;
    claimBadge = `<span class="claim-badge">👤 ${escapeHtml(isMine ? '나' : name)}</span>`;
  }

  const msgCount = thread.message_count || 1;
  const countLabel = msgCount > 1 ? ` <span class="card-thread-count">${msgCount}</span>` : '';
  const footerItems = [...badges];
  if (claimBadge) footerItems.push(claimBadge);

  return `
    <div class="kanban-card" data-id="${thread.conversation_id}">
      <div class="card-color-bar" style="background:${color}"></div>
      <div class="card-body">
        <div class="card-header-row">
          <div class="card-sender">${escapeHtml(thread.latest_sender || '(발신자 없음)')}${countLabel}</div>
          <div class="card-time">${time}</div>
        </div>
        <div class="card-subject">${escapeHtml(subject)}</div>
        <div class="card-preview">${escapeHtml(thread.body_preview || '')}</div>
        ${footerItems.length ? `<div class="card-footer">${footerItems.join('')}</div>` : ''}
      </div>
    </div>
  `;
}

/* ─── Sortable ──────────────────────────────────────────────────── */
function initSortable() {
  const lists = document.querySelectorAll('.column-body');
  lists.forEach(list => {
    Sortable.create(list, {
      group: 'kanban',
      animation: 150,
      ghostClass: 'sortable-ghost',
      chosenClass: 'sortable-chosen',
      onEnd: async (evt) => {
        const conversationId = evt.item.dataset.id;
        const newStatus = evt.to.dataset.status;
        if (evt.from === evt.to) return;

        await updateThreadStatus(newStatus, conversationId);

        const thread = state.threads.find(t => t.conversation_id === conversationId);
        if (thread) thread.status = newStatus;
        renderKanban();
      },
    });
  });
}

/* ─── Panel ─────────────────────────────────────────────────────── */
async function openPanel(conversationId) {
  state.currentConversationId = conversationId;
  state.activeTab = 'mail';
  const panel = document.getElementById('detail-panel');
  panel.classList.add('open');
  closeReplyForm();

  document.getElementById('panel-title').textContent = '불러오는 중...';
  document.getElementById('panel-meta').innerHTML = '<span class="spinner"></span>';
  document.getElementById('thread-timeline').innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  switchTab('mail');

  try {
    const res = await fetch(`/api/threads/${conversationId}`);
    if (!res.ok) throw new Error('불러오기 실패');
    const thread = await res.json();
    state.currentThread = thread;

    renderPanel(thread);
    setupReplyForm(thread);
    renderClaimBar(conversationId);
  } catch (e) {
    showToast('상세 정보 로드 실패: ' + e.message, 'error');
  }
}

function closePanel() {
  document.getElementById('detail-panel').classList.remove('open');
  closeReplyForm();
  state.currentConversationId = null;
  state.currentThread = null;
}

/* ─── 탭 전환 ─────────────────────────────────────────────────────── */
function switchTab(tab) {
  state.activeTab = tab;
  document.getElementById('tab-mail').classList.toggle('active', tab === 'mail');
  document.getElementById('tab-comments').classList.toggle('active', tab === 'comments');

  const mailContent = document.getElementById('tab-content-mail');
  const commentsContent = document.getElementById('tab-content-comments');

  if (tab === 'mail') {
    mailContent.classList.remove('hidden');
    commentsContent.classList.remove('visible');
    commentsContent.style.display = 'none';
  } else {
    mailContent.classList.add('hidden');
    commentsContent.classList.add('visible');
    commentsContent.style.display = 'flex';
    loadComments(state.currentConversationId);
  }
}

/* ─── 클레임 ─────────────────────────────────────────────────────── */
async function renderClaimBar(conversationId) {
  const thread = state.threads.find(t => t.conversation_id === conversationId);
  const bar = document.getElementById('panel-claim-bar');
  const label = document.getElementById('panel-claim-label');
  const btnClaim = document.getElementById('btn-claim');
  const btnUnclaim = document.getElementById('btn-unclaim');

  if (!bar) return;
  bar.style.display = 'flex';

  if (thread && thread.claimed_by) {
    const isMine = thread.claimed_by === (state.currentUser && state.currentUser.user_id);
    const name = thread.claimed_by_name || thread.claimed_by;
    label.textContent = isMine ? '내가 처리 중' : `${name}님이 처리 중`;
    btnClaim.style.display = 'none';
    btnUnclaim.style.display = isMine || (state.currentUser && state.currentUser.role === 'admin') ? '' : 'none';
  } else {
    label.textContent = '미클레임';
    btnClaim.style.display = '';
    btnUnclaim.style.display = 'none';
  }
}

async function claimThread() {
  const cid = state.currentConversationId;
  if (!cid) return;
  try {
    const res = await fetch(`/api/threads/${cid}/claim`, {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'include',
    });
    if (res.status === 409) {
      const err = await res.json();
      showToast(err.detail, 'error');
      return;
    }
    if (!res.ok) throw new Error('클레임 실패');
    showToast('클레임되었습니다.', 'success');
    await loadAllThreads();
    renderClaimBar(cid);
  } catch (e) {
    showToast('클레임 실패: ' + e.message, 'error');
  }
}

async function unclaimThread() {
  const cid = state.currentConversationId;
  if (!cid) return;
  try {
    const res = await fetch(`/api/threads/${cid}/claim`, {
      method: 'DELETE',
      headers: csrfHeaders(),
      credentials: 'include',
    });
    if (!res.ok) throw new Error('해제 실패');
    showToast('클레임이 해제되었습니다.', 'success');
    await loadAllThreads();
    renderClaimBar(cid);
  } catch (e) {
    showToast('해제 실패: ' + e.message, 'error');
  }
}

/* ─── 댓글 ──────────────────────────────────────────────────────── */
async function loadComments(conversationId) {
  if (!conversationId) return;
  const list = document.getElementById('comments-list');
  list.innerHTML = '<div style="color:#8b949e;font-size:13px;text-align:center;padding:20px">불러오는 중...</div>';

  try {
    const res = await fetch(`/api/threads/${conversationId}/comments`, { credentials: 'include' });
    if (!res.ok) {
      list.innerHTML = '<div style="color:#f85149;font-size:13px;text-align:center;padding:20px">댓글을 불러올 수 없습니다.</div>';
      return;
    }
    const data = await res.json();
    const comments = data.comments || [];

    const badge = document.getElementById('comment-count-badge');
    if (badge) {
      badge.style.display = comments.length ? '' : 'none';
      badge.textContent = comments.length;
    }

    if (!comments.length) {
      list.innerHTML = '<div style="color:#8b949e;font-size:13px;text-align:center;padding:40px">아직 팀 댓글이 없습니다. 첫 댓글을 남겨보세요!</div>';
      return;
    }

    list.innerHTML = comments.map(c => renderComment(c)).join('');
    list.scrollTop = list.scrollHeight;
  } catch (e) {
    list.innerHTML = '<div style="color:#f85149;font-size:13px;text-align:center;padding:20px">댓글을 불러올 수 없습니다.</div>';
  }
}

function renderComment(c) {
  const time = formatTime(c.created_at);
  const content = escapeHtml(c.content).replace(/@\[([^\]]+)\]\([^)]+\)/g, '<span class="mention-tag">@$1</span>');
  return `
    <div class="comment-item">
      <div>
        <span class="comment-author">${escapeHtml(c.display_name || c.user_id)}</span>
        <span class="comment-time">${time}</span>
      </div>
      <div class="comment-content">${content}</div>
    </div>
  `;
}

async function submitComment() {
  const cid = state.currentConversationId;
  const body = document.getElementById('comment-body').value.trim();
  if (!body || !cid) return;

  try {
    const res = await fetch(`/api/threads/${cid}/comments`, {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        content: body,
        project_id: state.activeProjectId || null,
      }),
    });
    if (!res.ok) throw new Error('전송 실패');
    document.getElementById('comment-body').value = '';
    await loadComments(cid);
  } catch (e) {
    showToast('댓글 전송 실패: ' + e.message, 'error');
  }
}

function renderPanel(thread) {
  document.getElementById('panel-title').textContent = thread.subject || '(제목 없음)';
  document.getElementById('panel-status').value = thread.status;

  const meta = document.getElementById('panel-meta');
  const metaBadges = [];
  if (thread.primary_folder) metaBadges.push(`<span class="badge badge-folder">📁 ${escapeHtml(thread.primary_folder)}</span>`);
  metaBadges.push(`<span class="badge badge-count">💬 ${thread.message_count}건</span>`);
  if (thread.has_folder_mismatch) metaBadges.push(`<span class="badge badge-mismatch">⚠️ 폴더 불일치</span>`);
  if (thread.has_new_reply) metaBadges.push(`<span class="badge badge-new-reply">🔴 새 회신</span>`);
  meta.innerHTML = metaBadges.join('');

  const timeline = document.getElementById('thread-timeline');
  let html = '';

  if (thread.has_folder_mismatch) {
    html += renderMismatchBanner(thread);
  }

  const messages = (thread.messages || []).slice().reverse();
  messages.forEach((msg, idx) => {
    const isNew = idx === 0 && thread.has_new_reply;
    const isCollapsed = messages.length > 1 && idx > 0;
    html += renderMessage(msg, isNew, null, isCollapsed);
  });

  timeline.innerHTML = html || '<div class="empty-state"><div class="empty-state-icon">📭</div><div>메시지가 없습니다</div></div>';
}

function renderMismatchBanner(thread) {
  const messages = thread.messages || [];
  const folderMap = {};
  messages.forEach(m => {
    if (m.folder_name) {
      folderMap[m.folder_name] = (folderMap[m.folder_name] || 0) + 1;
    }
  });

  const folderList = Object.entries(folderMap).map(([name, count]) =>
    `<div>📁 ${escapeHtml(name)}: ${count}건</div>`
  ).join('');

  const foldersInThread = Object.keys(folderMap);
  const btns = foldersInThread.map(fname => {
    const folder = state.folders.find(f => f.folder_name === fname);
    if (!folder) return '';
    return `<button class="btn-mismatch" onclick="moveThreadToFolder('${folder.folder_id}', '${escapeHtml(fname)}')">
      ${escapeHtml(fname)}으로 전체 이동
    </button>`;
  }).join('');

  return `
    <div class="mismatch-banner">
      <div class="mismatch-title">⚠️ 폴더 불일치</div>
      <div class="mismatch-folders">${folderList}</div>
      <div class="mismatch-actions">
        ${btns}
        <button class="btn-mismatch" onclick="this.closest('.mismatch-banner').remove()">무시</button>
      </div>
    </div>
  `;
}

function renderMessage(msg, isNew, prevMsg, isCollapsed = false) {
  const isOutgoing = msg.is_from_me;
  const cls = isOutgoing ? 'outgoing' : 'incoming';
  const collapsedCls = isCollapsed ? ' collapsed' : '';
  const timeStr = formatDateTime(msg.received_at);

  const toList = (msg.to_recipients || []).map(r => r.email).join(', ');
  const ccList = (msg.cc_recipients || []).map(r => r.email).join(', ');

  let recipientDiff = '';
  if (prevMsg && !isOutgoing) {
    const prevAll = new Set([...(prevMsg.to_recipients || []), ...(prevMsg.cc_recipients || [])].map(r => r.email));
    const currAll = new Set([...(msg.to_recipients || []), ...(msg.cc_recipients || [])].map(r => r.email));

    const added = [...currAll].filter(e => !prevAll.has(e));
    const removed = [...prevAll].filter(e => !currAll.has(e));

    if (added.length || removed.length) {
      recipientDiff = '<div style="margin-top:4px;font-size:10px">';
      added.forEach(e => { recipientDiff += `<span class="diff-added">(+) ${escapeHtml(e)} 추가됨</span><br>`; });
      removed.forEach(e => { recipientDiff += `<span class="diff-removed">(-) ${escapeHtml(e)} 제거됨</span><br>`; });
      recipientDiff += '</div>';
    }
  }

  const msgId = `msg-${msg.id.replace(/[^a-z0-9]/gi, '_')}`;
  const rawMsgId = msg.id;
  const previewText = (msg.body_preview || '').replace(/<[^>]+>/g, '').slice(0, 120);

  return `
    <div class="msg-bubble ${cls}${collapsedCls}" data-msg-id="${escapeHtml(rawMsgId)}">
      <div class="msg-header">
        <div class="msg-sender-area" onclick="toggleMsgCollapse(this.closest('.msg-bubble'))">
          <div class="msg-sender">
            ${isOutgoing ? '나 (발신)' : escapeHtml(msg.sender)}
            ${isNew ? '<span class="msg-new-badge">NEW</span>' : ''}
          </div>
          <div class="msg-time">${timeStr}</div>
        </div>
        <div class="msg-actions">
          <button class="msg-action-btn" onclick="openReplyForm('${escapeHtml(rawMsgId)}', 'reply')">↩ 답장</button>
          <button class="msg-action-btn" onclick="openReplyForm('${escapeHtml(rawMsgId)}', 'replyAll')">↩ 전체 답장</button>
          <button class="msg-action-btn" onclick="openReplyForm('${escapeHtml(rawMsgId)}', 'forward')">→ 전달</button>
        </div>
      </div>
      <div class="msg-preview">${escapeHtml(previewText)}</div>
      <div class="msg-recipients">
        ${toList ? `To: ${escapeHtml(toList)}` : ''}
        ${ccList ? `<br>CC: ${escapeHtml(ccList)}` : ''}
        ${recipientDiff}
      </div>
      <div class="msg-body" id="${msgId}">${sanitizeBody(msg.body || msg.body_preview || '')}</div>
      <button class="msg-expand-btn" onclick="toggleMsgExpand('${msgId}', this)">더 보기</button>
      ${renderAttachments(msg)}
    </div>
  `;
}

function formatFileSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileIcon(contentType, name) {
  const ext = (name || '').split('.').pop().toLowerCase();
  if (['jpg','jpeg','png','gif','bmp','webp','svg'].includes(ext) || (contentType || '').startsWith('image/')) return '🖼️';
  if (['pdf'].includes(ext)) return '📄';
  if (['doc','docx'].includes(ext)) return '📝';
  if (['xls','xlsx','csv'].includes(ext)) return '📊';
  if (['ppt','pptx'].includes(ext)) return '📊';
  if (['zip','rar','7z','tar','gz'].includes(ext)) return '📦';
  if (['mp4','avi','mov','wmv'].includes(ext)) return '🎬';
  if (['mp3','wav','aac'].includes(ext)) return '🎵';
  return '📎';
}

function renderAttachments(msg) {
  const atts = msg.attachments || [];
  if (atts.length === 0) {
    return msg.has_attachments ? '<div class="msg-attachments"><div class="msg-attachment-note">📎 첨부파일 로드 중...</div></div>' : '';
  }
  const chips = atts.map(att => {
    const icon = getFileIcon(att.content_type, att.name);
    const size = att.size ? ` (${formatFileSize(att.size)})` : '';
    const url = `/api/messages/${encodeURIComponent(msg.id)}/attachments/${encodeURIComponent(att.id)}`;
    return `<a class="msg-attachment-chip" href="${url}" download="${escapeHtml(att.name)}" title="${escapeHtml(att.name)}${size}">
      <span class="attachment-icon">${icon}</span>
      <span class="attachment-name">${escapeHtml(att.name)}</span>
      <span class="attachment-size">${size}</span>
    </a>`;
  }).join('');
  return `<div class="msg-attachments">${chips}</div>`;
}

function toggleMsgCollapse(el) {
  const bubble = el.classList.contains('msg-bubble') ? el : el.closest('.msg-bubble');
  bubble.classList.toggle('collapsed');
}

function toggleMsgExpand(id, btn) {
  const el = document.getElementById(id);
  if (el.classList.contains('msg-body-full')) {
    el.classList.remove('msg-body-full');
    btn.textContent = '더 보기';
  } else {
    el.classList.add('msg-body-full');
    btn.textContent = '접기';
  }
}

function sanitizeBody(html) {
  const div = document.createElement('div');
  div.innerHTML = html;
  const scripts = div.querySelectorAll('script, style, link');
  scripts.forEach(s => s.remove());
  return div.innerHTML;
}

/* ─── Reply Form ────────────────────────────────────────────────── */
function openReplyForm(msgId, type) {
  const thread = state.currentThread;
  if (!thread) return;

  const messages = thread.messages || [];
  const msg = messages.find(m => m.id === msgId) || messages[messages.length - 1];

  state.toRecipients = [];
  state.ccRecipients = [];
  state.bccRecipients = [];
  document.getElementById('reply-body').value = '';

  const typeSelect = document.getElementById('reply-type');
  typeSelect.value = type;

  if (msg) {
    if (type === 'reply') {
      state.toRecipients = msg.is_from_me
        ? [...(msg.to_recipients || [])]
        : [{ name: msg.sender, email: msg.sender_email }];
      state.ccRecipients = [];
    } else if (type === 'replyAll') {
      state.toRecipients = msg.is_from_me
        ? [...(msg.to_recipients || [])]
        : [{ name: msg.sender, email: msg.sender_email }];
      state.ccRecipients = [...(msg.cc_recipients || [])];
    } else {
      state.toRecipients = [];
      state.ccRecipients = [];
    }
  }

  state.bccRecipients = [];
  state.originalRecipients = {
    to: [...state.toRecipients],
    cc: [...state.ccRecipients],
    bcc: [],
  };

  renderRecipientTags('to');
  renderRecipientTags('cc');
  renderRecipientTags('bcc');

  const replyArea = document.getElementById('reply-area');
  replyArea.style.display = '';
  replyArea.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeReplyForm() {
  document.getElementById('reply-area').style.display = 'none';
}

function setupReplyForm(thread) {
  const messages = thread.messages || [];
  const lastMsg = messages[messages.length - 1];

  state.toRecipients = [];
  state.ccRecipients = [];
  state.bccRecipients = [];
  document.getElementById('reply-body').value = '';
  document.getElementById('reply-type').value = 'reply';

  if (lastMsg) {
    if (lastMsg.is_from_me) {
      state.toRecipients = [...(lastMsg.to_recipients || [])];
      state.ccRecipients = [...(lastMsg.cc_recipients || [])];
    } else {
      state.toRecipients = [{ name: lastMsg.sender, email: lastMsg.sender_email }];
      state.ccRecipients = [...(lastMsg.cc_recipients || [])];
    }
  }

  state.originalRecipients = {
    to: [...state.toRecipients],
    cc: [...state.ccRecipients],
    bcc: [],
  };

  renderRecipientTags('to');
  renderRecipientTags('cc');
  renderRecipientTags('bcc');
}

function onReplyTypeChange() {
  const type = document.getElementById('reply-type').value;
  const thread = state.currentThread;
  if (!thread) return;

  const messages = thread.messages || [];
  const lastMsg = messages[messages.length - 1];

  if (type === 'reply') {
    if (lastMsg) {
      state.toRecipients = lastMsg.is_from_me
        ? [...(lastMsg.to_recipients || [])]
        : [{ name: lastMsg.sender, email: lastMsg.sender_email }];
      state.ccRecipients = [];
    }
  } else if (type === 'replyAll') {
    if (lastMsg) {
      const to = lastMsg.is_from_me
        ? [...(lastMsg.to_recipients || [])]
        : [{ name: lastMsg.sender, email: lastMsg.sender_email }];
      state.toRecipients = to;
      state.ccRecipients = [...(lastMsg.cc_recipients || [])];
    }
  } else {
    state.toRecipients = [];
    state.ccRecipients = [];
  }

  state.bccRecipients = [];
  renderRecipientTags('to');
  renderRecipientTags('cc');
  renderRecipientTags('bcc');
}

function renderRecipientTags(field) {
  const map = { to: state.toRecipients, cc: state.ccRecipients, bcc: state.bccRecipients };
  const container = document.getElementById(`${field}-tags`);
  const input = document.getElementById(`${field}-input`);
  const autocomplete = document.getElementById(`${field}-autocomplete`);

  const tags = map[field].map((r, i) => `
    <div class="recipient-tag">
      <span>${escapeHtml(r.name || r.email)}</span>
      <button class="tag-remove" onclick="removeRecipient('${field}', ${i})">×</button>
    </div>
  `).join('');

  container.innerHTML = tags;
  container.appendChild(input);
  container.appendChild(autocomplete);
}

function removeRecipient(field, idx) {
  const map = { to: state.toRecipients, cc: state.ccRecipients, bcc: state.bccRecipients };
  map[field].splice(idx, 1);
  renderRecipientTags(field);
}

function addRecipient(field, recipient) {
  const map = { to: state.toRecipients, cc: state.ccRecipients, bcc: state.bccRecipients };
  const exists = map[field].some(r => r.email.toLowerCase() === recipient.email.toLowerCase());
  if (!exists) {
    map[field].push(recipient);
    renderRecipientTags(field);
  }
  document.getElementById(`${field}-input`).value = '';
  document.getElementById(`${field}-autocomplete`).style.display = 'none';
}

function focusInput(container) {
  const input = container.querySelector('.recipient-input');
  if (input) input.focus();
}

let autocompleteTimers = {};
async function onRecipientInput(input, field) {
  const q = input.value.trim();
  const dropdown = document.getElementById(`${field}-autocomplete`);

  if (!q || q.length < 2) {
    dropdown.style.display = 'none';
    return;
  }

  clearTimeout(autocompleteTimers[field]);
  autocompleteTimers[field] = setTimeout(async () => {
    try {
      const res = await fetch(`/api/people/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      const people = data.people || [];

      if (!people.length) {
        dropdown.style.display = 'none';
        return;
      }

      dropdown.innerHTML = '';
      people.forEach(p => {
        const item = document.createElement('div');
        item.className = 'autocomplete-item';
        item.innerHTML = `<span class="autocomplete-name">${escapeHtml(p.name)}</span><span class="autocomplete-email">${escapeHtml(p.email)}</span>`;
        item.addEventListener('click', () => addRecipient(field, p));
        dropdown.appendChild(item);
      });
      dropdown.style.display = 'block';
    } catch (e) {}
  }, 300);
}

function onRecipientKeydown(e, input, field) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const val = input.value.trim().replace(/,$/, '');
    if (val && val.includes('@')) {
      addRecipient(field, { name: val, email: val });
    }
  } else if (e.key === 'Escape') {
    document.getElementById(`${field}-autocomplete`).style.display = 'none';
  }
}

document.addEventListener('click', (e) => {
  ['to', 'cc', 'bcc'].forEach(field => {
    const dropdown = document.getElementById(`${field}-autocomplete`);
    if (dropdown && !e.target.closest(`#${field}-tags`)) {
      dropdown.style.display = 'none';
    }
  });
});

/* ─── Send Reply ────────────────────────────────────────────────── */
async function sendReply() {
  if (!state.currentConversationId) return;

  if (!state.toRecipients.length) {
    showToast('받는 사람을 입력해주세요', 'error');
    return;
  }

  const body = document.getElementById('reply-body').value.trim();
  if (!body) {
    showToast('회신 내용을 입력해주세요', 'error');
    return;
  }

  // 수신자 변경 감지
  const origTo = state.originalRecipients.to.map(r => r.email.toLowerCase()).sort();
  const currTo = state.toRecipients.map(r => r.email.toLowerCase()).sort();
  const origCc = state.originalRecipients.cc.map(r => r.email.toLowerCase()).sort();
  const currCc = state.ccRecipients.map(r => r.email.toLowerCase()).sort();

  const toChanged = JSON.stringify(origTo) !== JSON.stringify(currTo);
  const ccChanged = JSON.stringify(origCc) !== JSON.stringify(currCc);

  if (toChanged || ccChanged) {
    showRecipientConfirm();
    return;
  }

  await doSendReply();
}

function showRecipientConfirm() {
  const origToSet = new Set(state.originalRecipients.to.map(r => r.email.toLowerCase()));
  const currToSet = new Set(state.toRecipients.map(r => r.email.toLowerCase()));

  const added = state.toRecipients.filter(r => !origToSet.has(r.email.toLowerCase()));
  const removed = state.originalRecipients.to.filter(r => !currToSet.has(r.email.toLowerCase()));

  let diffHtml = '<b>To 변경사항:</b><br>';
  added.forEach(r => { diffHtml += `<span class="diff-added">(+) ${escapeHtml(r.email)} 추가됨</span><br>`; });
  removed.forEach(r => { diffHtml += `<span class="diff-removed">(-) ${escapeHtml(r.email)} 제거됨</span><br>`; });

  const origCcSet = new Set(state.originalRecipients.cc.map(r => r.email.toLowerCase()));
  const currCcSet = new Set(state.ccRecipients.map(r => r.email.toLowerCase()));
  const ccAdded = state.ccRecipients.filter(r => !origCcSet.has(r.email.toLowerCase()));
  const ccRemoved = state.originalRecipients.cc.filter(r => !currCcSet.has(r.email.toLowerCase()));

  if (ccAdded.length || ccRemoved.length) {
    diffHtml += '<br><b>CC 변경사항:</b><br>';
    ccAdded.forEach(r => { diffHtml += `<span class="diff-added">(+) ${escapeHtml(r.email)} 추가됨</span><br>`; });
    ccRemoved.forEach(r => { diffHtml += `<span class="diff-removed">(-) ${escapeHtml(r.email)} 제거됨</span><br>`; });
  }

  document.getElementById('confirm-modal-body').innerHTML = `
    <p style="margin-bottom:10px">원래 수신자에서 변경사항이 있습니다:</p>
    ${diffHtml}
  `;
  document.getElementById('confirm-modal').classList.add('active');
}

async function confirmSend() {
  closeModal('confirm-modal');
  await doSendReply();
}

async function doSendReply() {
  const btn = document.getElementById('btn-send');
  btn.disabled = true;
  btn.textContent = '전송 중...';

  try {
    const res = await fetch(`/api/threads/${state.currentConversationId}/reply`, {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        body: document.getElementById('reply-body').value,
        reply_type: document.getElementById('reply-type').value,
        to_recipients: state.toRecipients,
        cc_recipients: state.ccRecipients,
        bcc_recipients: state.bccRecipients,
      }),
    });

    if (!res.ok) throw new Error('전송 실패');

    showToast('회신이 전송되었습니다! ✓', 'success');
    document.getElementById('reply-body').value = '';
    closeReplyForm();

    // 상태를 done으로 업데이트
    await updateThreadStatus('done', state.currentConversationId);
    const thread = state.threads.find(t => t.conversation_id === state.currentConversationId);
    if (thread) thread.status = 'done';
    renderKanban();

    closePanel();
  } catch (e) {
    showToast('전송 실패: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '전송 →';
  }
}

/* ─── Status Update ─────────────────────────────────────────────── */
async function updateThreadStatus(newStatus, conversationId) {
  const cid = conversationId || state.currentConversationId;
  if (!cid) return;

  try {
    const res = await fetch(`/api/threads/${cid}/status`, {
      method: 'PATCH',
      headers: csrfHeaders(),
      credentials: 'include',
      body: JSON.stringify({ status: newStatus }),
    });

    if (!res.ok) throw new Error('상태 변경 실패');

    const thread = state.threads.find(t => t.conversation_id === cid);
    if (thread) {
      thread.status = newStatus;
      thread.has_new_reply = false;
    }
    renderKanban();
  } catch (e) {
    showToast('상태 변경 실패: ' + e.message, 'error');
  }
}

/* ─── Folder Move ───────────────────────────────────────────────── */
async function moveThreadToFolder(folderId, folderName) {
  if (!state.currentConversationId) return;
  showToast(`모든 메일을 "${folderName}"으로 이동 중...`, 'info');

  try {
    const res = await fetch(`/api/threads/${state.currentConversationId}/move`, {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'include',
      body: JSON.stringify({ destination_folder_id: folderId }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '이동 실패', 'error');
      return;
    }
    const data = await res.json();
    showToast(`${data.moved}건의 메일이 이동되었습니다 ✓`, 'success');

    await openPanel(state.currentConversationId);
  } catch (e) {
    showToast('이동 실패: ' + e.message, 'error');
  }
}

/* ─── Sync ──────────────────────────────────────────────────────── */
async function triggerSync() {
  if (state.isSyncing) return;
  state.isSyncing = true;
  const icon = document.getElementById('sync-icon');
  icon.textContent = '⏳';

  try {
    const res = await fetch('/api/emails/sync', { method: 'POST', headers: csrfHeaders(), credentials: 'include' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 503) {
        showToast('Outlook이 연동되지 않았습니다. 설정에서 연동해주세요.', 'error');
      } else {
        showToast(err.detail || '동기화 실패', 'error');
      }
      return;
    }
    const data = await res.json();
    showToast(`${data.mode === 'full' ? '전체' : '증분'} 동기화 시작...`, 'info');
    setTimeout(async () => {
      await loadAllThreads();
    }, 2000);
  } catch (e) {
    showToast('동기화 실패: ' + e.message, 'error');
  } finally {
    state.isSyncing = false;
    icon.textContent = '🔄';
  }
}

function updateSyncTime() {
  const label = document.getElementById('sync-time-label');
  const lastSyncText = document.getElementById('last-sync-text');
  const now = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
  label.textContent = `마지막 동기화: ${now}`;
  lastSyncText.textContent = `마지막 동기화: ${now}`;
}

/* ─── WebSocket ─────────────────────────────────────────────────── */
function connectWebSocket() {
  const wsUrl = `ws://${location.host}/ws`;
  let ws;
  let retryCount = 0;

  function connect() {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      retryCount = 0;
    };

    ws.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        await handleWsMessage(msg);
      } catch (e) {}
    };

    ws.onclose = () => {
      const delay = Math.min(1000 * Math.pow(2, retryCount), 30000);
      retryCount++;
      setTimeout(connect, delay);
    };

    ws.onerror = () => { ws.close(); };
  }

  connect();
}

async function handleWsMessage(msg) {
  switch (msg.type) {
    case 'new_mail':
      showToast(`새 메일 ${msg.data.count}건이 도착했습니다`, 'info');
      await loadAllThreads();
      break;
    case 'status_change':
      const thread = state.threads.find(t => t.conversation_id === msg.data.conversation_id);
      if (thread) thread.status = msg.data.status;
      renderKanban();
      break;
    case 'sync_complete':
      updateSyncTime();
      await loadAllThreads();
      break;
    case 'folder_moved':
      await loadAllThreads();
      break;
    case 'new_reply_sent':
      await loadAllThreads();
      break;
    case 'claim_changed':
      await loadAllThreads();
      if (state.currentConversationId === msg.data.conversation_id) {
        renderClaimBar(msg.data.conversation_id);
      }
      break;
    case 'new_comment':
      if (state.currentConversationId === msg.data.conversation_id && state.activeTab === 'comments') {
        loadComments(msg.data.conversation_id);
      }
      break;
    case 'mention':
      showToast(`💬 ${msg.data.from_name}님이 멘션했습니다: ${msg.data.content_preview}`, 'info');
      break;
  }
}

/* ─── Modal ─────────────────────────────────────────────────────── */
function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

/* ─── Toast ─────────────────────────────────────────────────────── */
function showToast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  toast.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${escapeHtml(message)}</span>`;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

/* ─── Helpers ───────────────────────────────────────────────────── */
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit' });
  } catch (e) { return ''; }
}

function formatDateTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('ko-KR', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (e) { return isoStr; }
}
