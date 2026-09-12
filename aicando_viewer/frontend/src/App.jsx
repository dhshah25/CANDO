import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import {
  MessageSquare, Plus, Send, Bot, User, ChevronLeft, ChevronDown, ChevronUp,
  Menu, Clock, Dna, Brain, Wrench, FileSpreadsheet, X, FolderOpen, Eye, EyeOff, File,
  Pencil, Check
} from 'lucide-react';

const API_BASE = 'http://localhost:8001';

export default function App() {
  // Conversation state
  const [conversations, setConversations] = useState([]);
  const [activeConvo, setActiveConvo] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  // UI state
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [expandedCoT, setExpandedCoT] = useState({});
  const [workspacePanelOpen, setWorkspacePanelOpen] = useState(false);

  // Chat input state
  const [inputText, setInputText] = useState('');
  const [sending, setSending] = useState(false);
  const [editingIndex, setEditingIndex] = useState(null);
  const [editText, setEditText] = useState('');

  // Workspace file state
  const [workspaceFiles, setWorkspaceFiles] = useState([]);
  const [previewFile, setPreviewFile] = useState(null);
  const [previewData, setPreviewData] = useState(null);

  const messagesEndRef = useRef(null);

  // Load conversation list on mount
  useEffect(() => {
    axios.get(`${API_BASE}/api/conversations`)
      .then(res => setConversations(res.data.conversations))
      .catch(err => console.error('Failed to load conversations:', err));
  }, []);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  // ── Data Loading ──

  const loadConversation = async (id) => {
    setLoading(true);
    setExpandedCoT({});
    setPreviewFile(null);
    setPreviewData(null);
    try {
      const [convoRes, filesRes] = await Promise.all([
        axios.get(`${API_BASE}/api/conversations/${id}`),
        axios.get(`${API_BASE}/api/workspace/${id}/files`).catch(() => ({ data: { files: [] } })),
      ]);
      setActiveConvo(convoRes.data);
      setMessages(convoRes.data.messages || []);
      setWorkspaceFiles(filesRes.data.files || []);
    } catch (err) {
      console.error('Failed to load conversation:', err);
    }
    setLoading(false);
  };

  const openFilePreview = async (filename) => {
    if (!activeConvo) return;
    try {
      const res = await axios.get(`${API_BASE}/api/workspace/${activeConvo.id}/file/${filename}`);
      setPreviewFile(filename);
      setPreviewData(res.data);
    } catch (err) {
      console.error('Failed to load file:', err);
    }
  };

  // ── Chat Actions ──

  const startNewConversation = () => {
    const newId = `run-${Date.now()}`;
    setActiveConvo({
      id: newId,
      title: 'New Conversation',
      created_at: new Date().toISOString(),
      messages: [],
    });
    setMessages([]);
    setWorkspaceFiles([]);
    setWorkspacePanelOpen(false);
  };

  const handleSendMessage = async (e) => {
    if (e) e.preventDefault();
    if (!inputText.trim() || !activeConvo || sending) return;

    const userPrompt = inputText;
    setInputText('');
    setSending(true);

    // Show user message immediately
    setMessages(prev => [...prev, { role: 'user', content: userPrompt }]);

    try {
      const res = await axios.post(
        `${API_BASE}/api/conversations/${activeConvo.id}/message`,
        { prompt: userPrompt }
      );
      setMessages(prev => [...prev, res.data.message]);

      // Update title for new conversations
      if (activeConvo.title === 'New Conversation') {
        setActiveConvo(prev => ({
          ...prev,
          title: userPrompt.slice(0, 40) + (userPrompt.length > 40 ? '...' : '')
        }));
      }

      // Refresh sidebar and workspace files
      const listRes = await axios.get(`${API_BASE}/api/conversations`);
      setConversations(listRes.data.conversations);
      const filesRes = await axios.get(`${API_BASE}/api/workspace/${activeConvo.id}/files`);
      setWorkspaceFiles(filesRes.data.files || []);
    } catch (err) {
      console.error('Failed to send message:', err);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Failed to get a response. Please verify the backend is running.'
      }]);
    }
    setSending(false);
  };

  const handleEditMessage = async (index) => {
    if (!editText.trim() || sending) return;
    setSending(true);
    setEditingIndex(null);

    // Truncate conversation at edit point and re-send
    const truncated = messages.slice(0, index);
    truncated.push({ role: 'user', content: editText });
    setMessages(truncated);
    setEditText('');

    try {
      const res = await axios.post(
        `${API_BASE}/api/conversations/${activeConvo.id}/message`,
        { prompt: editText }
      );
      setMessages(prev => [...prev, res.data.message]);

      const listRes = await axios.get(`${API_BASE}/api/conversations`);
      setConversations(listRes.data.conversations);
      const filesRes = await axios.get(`${API_BASE}/api/workspace/${activeConvo.id}/files`);
      setWorkspaceFiles(filesRes.data.files || []);
    } catch (err) {
      console.error('Edit send failed:', err);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Failed to get a response. Please verify the backend is running.'
      }]);
    }
    setSending(false);
  };

  // ── Helpers ──

  const toggleCoT = (index) => {
    setExpandedCoT(prev => ({ ...prev, [index]: !prev[index] }));
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  };

  const getFileIcon = (type) => {
    if (type === 'table') return <FileSpreadsheet size={14} />;
    if (type === 'structure') return <Dna size={14} />;
    return <File size={14} />;
  };

  // Determine toggle button label based on available content
  const getToggleLabel = (msg, isExpanded) => {
    const hasCot = !!msg.chain_of_thought;
    const hasTools = msg.tool_calls?.length > 0;
    if (hasCot && hasTools) return isExpanded ? 'Hide reasoning & tools' : 'Show reasoning & tools';
    if (hasTools) return isExpanded ? 'Hide tools' : 'Show tools';
    return isExpanded ? 'Hide reasoning' : 'Show reasoning';
  };

  // ── Render ──

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : 'sidebar-closed'}`}>
        <div className="sidebar-header">
          <div className="sidebar-brand">
            <Dna size={20} className="brand-icon" />
            <span className="brand-text">AICANDO</span>
          </div>
          <button className="sidebar-toggle" onClick={() => setSidebarOpen(false)}>
            <ChevronLeft size={18} />
          </button>
        </div>

        <button className="new-chat-btn" onClick={startNewConversation}>
          <Plus size={16} /><span>New Conversation</span>
        </button>

        <div className="conversation-list">
          <span className="list-label">Recent</span>
          {conversations.length === 0 && <p className="empty-list">No conversations yet</p>}
          {conversations.map(c => (
            <button key={c.id}
              className={`convo-item ${activeConvo?.id === c.id ? 'convo-active' : ''}`}
              onClick={() => loadConversation(c.id)}>
              <MessageSquare size={14} className="convo-icon" />
              <div className="convo-meta">
                <span className="convo-title">{c.title}</span>
                <span className="convo-date">
                  <Clock size={10} /> {formatDate(c.created_at)}
                </span>
              </div>
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <span className="footer-text">AICANDO Conversation Viewer v2.0</span>
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="chat-main">
        <header className="chat-header">
          {!sidebarOpen && (
            <button className="menu-btn" onClick={() => setSidebarOpen(true)}>
              <Menu size={20} />
            </button>
          )}
          <div className="header-info">
            {activeConvo ? (
              <h2 className="header-title">{activeConvo.title}</h2>
            ) : (
              <h2 className="header-title">Select a conversation</h2>
            )}
          </div>
          {activeConvo && workspaceFiles.length > 0 && (
            <button className="workspace-toggle"
              onClick={() => setWorkspacePanelOpen(!workspacePanelOpen)}>
              <FolderOpen size={14} />
              <span>Files</span>
            </button>
          )}
        </header>

        <div className="chat-body">
          {/* Messages Container */}
          <div className={`messages-container ${workspacePanelOpen ? 'with-panel' : ''}`}>
            {/* Welcome screen when no conversation is selected */}
            {!activeConvo && !loading && (
              <div className="welcome-screen">
                <div className="welcome-icon-wrap"><Dna size={48} className="welcome-icon" /></div>
                <h1 className="welcome-title">AICANDO Conversation Viewer</h1>
                <p className="welcome-subtitle">
                  Browse and review LLM-powered analysis conversations.
                  Select a conversation from the sidebar to get started.
                </p>
                <div className="welcome-features">
                  <div className="feature-chip"><MessageSquare size={14} /><span>Rich Markdown</span></div>
                  <div className="feature-chip"><Brain size={14} /><span>CoT Filtering</span></div>
                  <div className="feature-chip"><Wrench size={14} /><span>Tool Call Display</span></div>
                  <div className="feature-chip"><FileSpreadsheet size={14} /><span>CSV/TSV Viewer</span></div>
                </div>
              </div>
            )}

            {loading && (
              <div className="loading-state"><div className="loading-spinner" /><p>Loading conversation...</p></div>
            )}

            {/* Message list */}
            {activeConvo && !loading && messages.map((msg, i) => (
              <div key={i} className={`message-row ${msg.role === 'assistant' ? 'msg-assistant' : 'msg-user'}`}>

                {/* Assistant message */}
                {msg.role === 'assistant' && (
                  <>
                    <div className="message-avatar">
                      <div className="avatar avatar-bot"><Bot size={18} /></div>
                    </div>
                    <div className="message-content">
                      <span className="message-role">AICANDO AI</span>

                      {/* Collapsible reasoning & tool calls */}
                      {(msg.chain_of_thought || msg.tool_calls?.length > 0) && (
                        <div className="cot-section" style={{ marginBottom: '16px' }}>
                          <button className="cot-toggle" onClick={() => toggleCoT(i)}>
                            {expandedCoT[i] ? (
                              <><EyeOff size={12} /><span>{getToggleLabel(msg, true)}</span><ChevronUp size={12} /></>
                            ) : (
                              <><Eye size={12} /><span>{getToggleLabel(msg, false)}</span><ChevronDown size={12} /></>
                            )}
                          </button>
                          {expandedCoT[i] && (
                            <div className="cot-content">
                              {msg.chain_of_thought && (
                                <div style={{ marginBottom: msg.tool_calls?.length > 0 ? '16px' : '0' }}>
                                  <div className="cot-label"><Brain size={11} /> Internal Chain-of-Thought</div>
                                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                    {msg.chain_of_thought}
                                  </ReactMarkdown>
                                </div>
                              )}
                              {msg.tool_calls?.length > 0 && (
                                <div>
                                  <div className="cot-label" style={{ marginBottom: '8px' }}><Wrench size={11} /> Tool Executions</div>
                                  <div className="tool-calls-container" style={{ marginBottom: 0 }}>
                                    {msg.tool_calls.map((tc, j) => {
                                      if (tc.type === 'tool_result' && tc.name === 'result') return null;
                                      return (
                                        <div key={j} className={`tool-call-badge ${tc.type === 'tool_result' ? 'tool-result' : ''}`}>
                                          <Wrench size={12} />
                                          <span className="tool-name">{tc.name}</span>
                                          {tc.arguments && (
                                            <span className="tool-args">
                                              ({Object.entries(tc.arguments).slice(0, 2).map(([k, v]) =>
                                                `${k}: "${String(v).slice(0, 30)}"`).join(', ')})
                                            </span>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Main message body */}
                      <div className="message-body">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                          {msg.content}
                        </ReactMarkdown>
                      </div>

                      {/* File reference badges */}
                      {msg.files?.length > 0 && (
                        <div className="file-refs">
                          {msg.files.map((fp, j) => {
                            const fname = fp.split('/').pop();
                            return (
                              <button key={j} className="file-ref-badge" onClick={() => openFilePreview(fp)}>
                                <FileSpreadsheet size={11} /><span>{fname}</span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </>
                )}

                {/* User message */}
                {msg.role === 'user' && (
                  <div className="user-bubble-wrap">
                    {editingIndex === i ? (
                      <div className="edit-prompt-box">
                        <textarea
                          className="edit-textarea"
                          value={editText}
                          onChange={e => setEditText(e.target.value)}
                          rows={3}
                        />
                        <div className="edit-actions">
                          <button className="edit-cancel" onClick={() => setEditingIndex(null)}>Cancel</button>
                          <button className="edit-submit" onClick={() => handleEditMessage(i)}>
                            <Check size={14} /> Submit
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="user-bubble-container">
                        <div className="user-bubble">
                          {msg.content}
                        </div>
                        <button
                          className="edit-btn"
                          title="Edit prompt"
                          onClick={() => { setEditingIndex(i); setEditText(msg.content); }}
                        >
                          <Pencil size={13} />
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Typing indicator */}
            {sending && (
              <div className="message-row msg-assistant">
                <div className="message-avatar">
                  <div className="avatar avatar-bot"><Bot size={18} /></div>
                </div>
                <div className="message-content">
                  <span className="message-role">AICANDO AI</span>
                  <div className="typing-indicator">
                    <span /><span /><span />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Workspace Panel */}
          {workspacePanelOpen && (
            <div className="workspace-panel">
              <div className="panel-header">
                <h3><FolderOpen size={14} /> Workspace Files</h3>
                <button onClick={() => setWorkspacePanelOpen(false)}><X size={16} /></button>
              </div>
              <div className="panel-file-list">
                {workspaceFiles.map((f, i) => {
                  const displayName = f.display_name || f.name;
                  const dirPrefix = f.name.includes('/') ? f.name.substring(0, f.name.lastIndexOf('/') + 1) : '';
                  return (
                    <button key={i} className={`panel-file-item ${previewFile === f.name ? 'panel-file-active' : ''}`}
                      onClick={() => openFilePreview(f.name)}>
                      {getFileIcon(f.type)}
                      <span className="panel-file-name">
                        {dirPrefix && <span className="panel-file-dir">{dirPrefix}</span>}
                        {displayName}
                      </span>
                      <span className="panel-file-size">{(f.size_bytes / 1024).toFixed(1)}KB</span>
                    </button>
                  );
                })}
              </div>

              {/* File Preview Pane */}
              {previewData && (
                <div className="file-preview">
                  <div className="preview-header">
                    <span>{previewFile}</span>
                    <button onClick={() => { setPreviewFile(null); setPreviewData(null); }}><X size={14} /></button>
                  </div>
                  {previewData.type === 'table' && (
                    <div className="preview-table-wrap">
                      <table className="preview-table">
                        <thead>
                          <tr>{previewData.headers.map((h, i) => <th key={i}>{h}</th>)}</tr>
                        </thead>
                        <tbody>
                          {previewData.rows.map((row, i) => (
                            <tr key={i}>
                              {previewData.headers.map((h, j) => <td key={j}>{row[h]}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {previewData.truncated && (
                        <p className="truncation-note">Showing first 500 of {previewData.total_rows} rows</p>
                      )}
                    </div>
                  )}
                  {previewData.type === 'text' && (
                    <pre className="preview-text">{previewData.content}</pre>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Chat Input Bar */}
        {activeConvo && (
          <form className="input-bar" onSubmit={handleSendMessage}>
            <div className="input-wrapper">
              <input
                type="text"
                placeholder={sending ? 'Processing...' : 'Ask a drug discovery question...'}
                value={inputText}
                onChange={e => setInputText(e.target.value)}
                disabled={sending}
                className="chat-input"
              />
              <button type="submit" className="send-btn" disabled={!inputText.trim() || sending}>
                <Send size={18} />
              </button>
            </div>
            <p className="input-disclaimer">Running securely on local computational workspace</p>
          </form>
        )}
      </main>
    </div>
  );
}
