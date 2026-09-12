import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Loader2, Database, Brain, Activity, ExternalLink, ShieldCheck, Dna, FlaskConical, Sparkles, Upload, FileText, ChevronDown, ChevronUp, BookOpen, ScrollText, AlertTriangle, Eye, XCircle, Quote, Beaker } from 'lucide-react';

export default function App() {
  const [meshId, setMeshId] = useState('');
  const [uploadedFile, setUploadedFile] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [loadingStage, setLoadingStage] = useState(0);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [showWatchList, setShowWatchList] = useState(false);
  const [showRejected, setShowRejected] = useState(false);
  const [showOtherCandidates, setShowOtherCandidates] = useState(false);
  const fileInputRef = useRef(null);

  // Only show links to actual papers (PMID URLs), not fallback search URLs
  const getRealPaperUrls = (urls) => (urls || []).filter(u => /\/\d+\/$/.test(u));

  const stages = [
    { text: "Parsing prediction file..." },
    { text: "Fetching PubMed abstracts from NCBI..." },
    { text: "Building multi-source evidence dossier..." },
    { text: "LLM applying pharmacological adjudication rules..." },
  ];

  useEffect(() => {
    let interval;
    if (isAnalyzing && loadingStage < stages.length - 1) {
      interval = setInterval(() => {
        setLoadingStage((prev) => Math.min(prev + 1, stages.length - 1));
      }, 3500);
    }
    return () => clearInterval(interval);
  }, [isAnalyzing, loadingStage]);

  const handleFileSelect = (file) => {
    if (file && (file.name.endsWith('.tsv') || file.name.endsWith('.csv'))) {
      setUploadedFile(file);
      setError(null);
    } else if (file) {
      setError('Unsupported format. Please upload a .tsv or .csv prediction file.');
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    handleFileSelect(e.dataTransfer.files[0]);
  };

  const handleAnalyze = async (e) => {
    e.preventDefault();
    if (!uploadedFile || !meshId) return;

    setIsAnalyzing(true);
    setLoadingStage(0);
    setResults(null);
    setError(null);
    setShowWatchList(false);
    setShowRejected(false);
    setShowOtherCandidates(false);

    try {
      const formData = new FormData();
      formData.append('mesh_id', meshId);
      formData.append('prediction_file', uploadedFile);

      const response = await axios.post('http://localhost:8000/api/analyze', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      setTimeout(() => {
        setResults(response.data.result);
        setIsAnalyzing(false);
      }, 800);
    } catch (err) {
      const backendError = err.response?.data?.detail;
      setError(backendError || err.message || 'Failed to connect to the backend server.');
      setIsAnalyzing(false);
    }
  };

  const getRankClass = (i) => i === 0 ? 'rank-1' : i === 1 ? 'rank-2' : i === 2 ? 'rank-3' : 'rank-default';
  const getRankLabel = (i) => i === 0 ? '🥇 #1' : i === 1 ? '🥈 #2' : i === 2 ? '🥉 #3' : `#${i + 1}`;

  const tierColors = {
    'A': { bg: 'rgba(52, 211, 153, 0.1)', border: 'rgba(52, 211, 153, 0.25)', text: '#34d399', label: 'Phase II/III Trial' },
    'B': { bg: 'rgba(96, 165, 250, 0.1)', border: 'rgba(96, 165, 250, 0.25)', text: '#60a5fa', label: 'Phase I / Preclinical' },
    'C': { bg: 'rgba(251, 191, 36, 0.1)', border: 'rgba(251, 191, 36, 0.25)', text: '#fbbf24', label: 'In Vitro / Cell-line' },
    'D': { bg: 'rgba(148, 163, 184, 0.08)', border: 'rgba(148, 163, 184, 0.15)', text: '#94a3b8', label: 'Observational' },
  };

  return (
    <div className="min-h-screen relative" style={{ background: 'var(--surface-dark)' }}>
      <div className="bg-mesh" />

      <div className="relative z-10 max-w-5xl mx-auto px-6 py-12 space-y-10">
        
        {/* ---- Header ---- */}
        <header className="text-center space-y-4 fade-in-up">
          <div className="inline-flex items-center justify-center mb-1">
            <div className="p-3.5 rounded-2xl" style={{ 
              background: 'var(--gradient-brand)', 
              boxShadow: '0 6px 30px rgba(129,140,248,0.25)' 
            }}>
              <ScrollText size={28} className="text-white" />
            </div>
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight" style={{ color: 'var(--text-primary)' }}>
            CANDO <span className="shimmer-text">LitReview</span>
          </h1>
          <p className="text-sm md:text-base font-medium leading-relaxed" style={{ 
            color: 'var(--text-muted)', maxWidth: 480, margin: '0 auto' 
          }}>
            AI-Powered Automated Literature Review for Drug Repurposing — 
            grounded in real PubMed evidence, filtered by pharmacological safety rules
          </p>
        </header>

        {/* ---- Control Panel ---- */}
        <section className="glass-card p-7 fade-in-up" style={{ animationDelay: '0.1s' }}>
          <form onSubmit={handleAnalyze} className="space-y-5">
            <div className="flex flex-col md:flex-row gap-4">
              <div className="flex-1 text-left">
                <label className="block text-[11px] font-bold uppercase tracking-[0.12em] mb-2.5" style={{ color: 'var(--text-muted)' }}>
                  Disease MeSH ID
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 flex items-center pl-4 pointer-events-none" style={{ color: 'var(--text-muted)' }}>
                    <Database size={15} />
                  </div>
                  <input 
                    type="text" className="mesh-input"
                    value={meshId}
                    onChange={(e) => setMeshId(e.target.value)}
                    placeholder="e.g. D001943"
                  />
                </div>
              </div>
              <div className="flex items-end">
                <button type="submit" disabled={isAnalyzing || !meshId || !uploadedFile} className="btn-primary">
                  {isAnalyzing ? <Loader2 className="animate-spin" size={17} /> : <Sparkles size={17} />}
                  <span>{isAnalyzing ? "Analyzing..." : "Run Literature Review"}</span>
                </button>
              </div>
            </div>

            {/* File Upload Zone */}
            <div 
              className="file-drop-zone"
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              style={{
                borderColor: isDragging ? 'var(--accent-primary)' : uploadedFile ? 'rgba(52, 211, 153, 0.4)' : undefined,
                background: isDragging ? 'rgba(129, 140, 248, 0.04)' : uploadedFile ? 'rgba(52, 211, 153, 0.03)' : undefined,
                boxShadow: uploadedFile ? '0 0 20px rgba(52, 211, 153, 0.06)' : undefined,
              }}
            >
              <input ref={fileInputRef} type="file" accept=".tsv,.csv" 
                onChange={(e) => handleFileSelect(e.target.files[0])} style={{ display: 'none' }} />
              {uploadedFile ? (
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg" style={{ background: 'rgba(52, 211, 153, 0.1)' }}>
                    <FileText size={18} style={{ color: '#34d399' }} />
                  </div>
                  <div className="text-left">
                    <p className="text-sm font-semibold" style={{ color: '#34d399' }}>{uploadedFile.name}</p>
                    <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                      {(uploadedFile.size / 1024).toFixed(1)} KB · Click or drop to replace
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2.5 py-2">
                  <div className="p-2.5 rounded-xl" style={{ background: 'rgba(129, 140, 248, 0.06)' }}>
                    <Upload size={22} style={{ color: isDragging ? 'var(--accent-primary)' : 'var(--text-muted)' }} />
                  </div>
                  <div>
                    <p className="text-sm font-medium" style={{ color: 'var(--text-muted)' }}>
                      Drop CANDO prediction file here or <span style={{ color: 'var(--accent-primary)', fontWeight: 600 }}>browse</span>
                    </p>
                    <p className="text-xs mt-1" style={{ color: 'var(--text-muted)', opacity: 0.5 }}>
                      .tsv or .csv · must contain compound names
                    </p>
                  </div>
                </div>
              )}
            </div>
          </form>
        </section>

        {/* ---- Error State ---- */}
        {error && (
          <div className="fade-in-up p-5 rounded-2xl flex items-start gap-3" style={{ 
            background: 'rgba(239, 68, 68, 0.06)', border: '1px solid rgba(239, 68, 68, 0.15)',
          }}>
            <span className="text-lg leading-none mt-0.5">⚠️</span>
            <div>
              <p className="text-sm font-semibold" style={{ color: '#fca5a5' }}>Analysis Failed</p>
              <p className="text-sm mt-1 leading-relaxed" style={{ color: 'rgba(252, 165, 165, 0.7)' }}>{error}</p>
            </div>
          </div>
        )}

        {/* ---- Loading Sequence ---- */}
        {isAnalyzing && (
          <div className="glass-card p-14 flex flex-col items-center justify-center space-y-8 text-center fade-in-up">
            <div className="relative">
              <div className="absolute rounded-full pulse-ring" style={{ 
                background: 'rgba(129, 140, 248, 0.12)', width: 76, height: 76, top: -6, left: -6 
              }} />
              <div className="relative p-4 rounded-full" style={{ 
                background: 'var(--gradient-brand)', boxShadow: '0 6px 30px rgba(129,140,248,0.3)' 
              }}>
                <Activity size={30} className="text-white" />
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-center gap-2.5">
                <Loader2 className="animate-spin" size={16} style={{ color: 'var(--accent-primary)' }} />
                <h3 className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>
                  {stages[loadingStage].text}
                </h3>
              </div>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Cross-referencing clinical abstracts with pharmacological safety rules...
              </p>
            </div>
            <div className="flex items-center gap-2">
              {stages.map((_, i) => (
                <div key={i} className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full transition-all duration-700" style={{
                    background: i <= loadingStage ? 'var(--accent-primary)' : 'rgba(129,140,248,0.1)',
                    boxShadow: i <= loadingStage ? '0 0 10px rgba(129,140,248,0.4)' : 'none',
                    transform: i <= loadingStage ? 'scale(1.2)' : 'scale(1)',
                  }} />
                  {i < stages.length - 1 && (
                    <div className="w-10 h-px rounded" style={{ 
                      background: i < loadingStage ? 'var(--accent-primary)' : 'rgba(129,140,248,0.06)' 
                    }} />
                  )}
                </div>
              ))}
            </div>
            <div className="w-full max-w-sm">
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${((loadingStage + 1) / stages.length) * 100}%` }} />
              </div>
            </div>
          </div>
        )}

        {/* ════════════════════ RESULTS DASHBOARD ════════════════════ */}
        {results && !isAnalyzing && (
          <div className="space-y-8 fade-in-up">

            {/* Dossier Quality Note */}
            {results.dossier_quality_note && (
              <div className="p-4 rounded-2xl flex items-start gap-3" style={{
                background: 'rgba(129, 140, 248, 0.04)', border: '1px solid var(--border-subtle)',
              }}>
                <Brain size={16} style={{ color: 'var(--accent-primary)', marginTop: 2, flexShrink: 0 }} />
                <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                  <span className="font-bold">AI Assessment:</span> {results.dossier_quality_note}
                </p>
              </div>
            )}

            {/* ──── SECTION: Verified Recommendations ──── */}
            <div className="flex items-center gap-4">
              <div className="p-2.5 rounded-xl" style={{ background: 'rgba(52, 211, 153, 0.08)', border: '1px solid rgba(52, 211, 153, 0.12)' }}>
                <ShieldCheck size={20} style={{ color: '#34d399' }} />
              </div>
              <div>
                <h2 className="text-xl md:text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>
                  Verified Recommendations
                </h2>
                <p className="text-xs md:text-sm mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{results.disease_name}</span> · MeSH: {meshId} · {results.top_candidates.length} passed all rules
                </p>
              </div>
            </div>

            {results.top_candidates.length === 0 && (
              <div className="empty-state">
                <p className="text-lg font-semibold" style={{ color: '#fbbf24' }}>Zero Candidates Passed Safety Filtering</p>
                <p className="mt-3 text-sm leading-relaxed" style={{ color: 'var(--text-muted)', maxWidth: 520, margin: '12px auto 0' }}>
                  All analyzed compounds were filtered out by the pharmacological adjudication rules.
                  This is a valid outcome — the AI returned an empty set to prevent false therapeutic claims.
                </p>
              </div>
            )}

            {/* Top Candidate Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 stagger-children">
              {results.top_candidates.map((drug, index) => {
                const tier = tierColors[drug.evidence_tier] || tierColors['D'];
                return (
                  <div key={index} className="drug-card flex flex-col">
                    {/* Header */}
                    <div className="px-6 pt-5 pb-4" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <div className="flex items-start justify-between mb-3">
                        <span className={`rank-badge ${getRankClass(index)}`}>{getRankLabel(index)}</span>
                        {drug.evidence_tier && (
                          <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-md" style={{
                            background: tier.bg, border: `1px solid ${tier.border}`, color: tier.text,
                          }}>
                            Tier {drug.evidence_tier}
                          </span>
                        )}
                      </div>
                      <h3 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{drug.compound}</h3>
                      {drug.mechanism && (
                        <p className="text-[11px] mt-1.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                          {drug.mechanism}
                        </p>
                      )}
                    </div>
                    
                    {/* Summary */}
                    <div className="px-6 py-5 flex-grow space-y-3">
                      <div className="flex items-center gap-1.5">
                        <Brain size={12} style={{ color: 'var(--accent-primary)' }} />
                        <span className="text-[10px] font-bold uppercase tracking-[0.12em]" style={{ color: 'var(--text-muted)' }}>
                          AI Literature Synthesis
                        </span>
                        {drug.consensus && (
                          <span className="ml-auto text-[9px] font-bold uppercase px-2 py-0.5 rounded-full"
                            style={{ background: 'rgba(52, 211, 153, 0.1)', color: '#34d399', border: '1px solid rgba(52, 211, 153, 0.2)' }}>
                            Consensus
                          </span>
                        )}
                      </div>
                      <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                        {drug.llm_summary}
                      </p>

                      {/* Quoted Evidence */}
                      {drug.key_quoted_evidence && (
                        <div className="p-3 rounded-lg" style={{ background: 'rgba(129, 140, 248, 0.04)', border: '1px solid var(--border-subtle)' }}>
                          <div className="flex items-center gap-1.5 mb-1.5">
                            <Quote size={10} style={{ color: 'var(--accent-secondary)' }} />
                            <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                              Key Evidence
                            </span>
                          </div>
                          <p className="text-[11px] leading-relaxed italic" style={{ color: 'var(--text-muted)' }}>
                            {drug.key_quoted_evidence}
                          </p>
                        </div>
                      )}
                    </div>

                    {/* Paper Links */}
                    <div className="px-6 pb-5 mt-auto space-y-2">
                      {getRealPaperUrls(drug.pubmed_urls).length > 0 && (
                        <span className="text-[10px] font-bold uppercase tracking-[0.12em]" style={{ color: 'var(--text-muted)' }}>
                          Source Papers
                        </span>
                      )}
                      {getRealPaperUrls(drug.pubmed_urls).map((url, i) => (
                        <a key={i} href={url} target="_blank" rel="noreferrer" className="pubmed-link">
                          <BookOpen size={12} />
                          <span>PubMed Paper {i + 1}</span>
                          <ExternalLink size={10} style={{ marginLeft: 'auto', opacity: 0.5 }} />
                        </a>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* ──── SECTION: Watch List ──── */}
            {results.watch_list?.length > 0 && (
              <div className="glass-card overflow-hidden">
                <button onClick={() => setShowWatchList(!showWatchList)} className="expand-btn">
                  <div className="flex items-center gap-2.5">
                    <Eye size={15} style={{ color: '#fbbf24' }} />
                    <span>Watch List — Tier D Evidence ({results.watch_list.length})</span>
                  </div>
                  {showWatchList ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {showWatchList && (
                  <>
                    <hr className="section-divider" />
                    <div className="p-6 space-y-3">
                      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                        These drugs showed weak or observational-only evidence. They are not recommended but may warrant future investigation.
                      </p>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        {results.watch_list.map((drug, i) => (
                          <div key={i} className="candidate-mini">
                            <div className="flex items-start justify-between mb-2">
                              <h4 className="text-sm font-bold" style={{ color: '#fbbf24' }}>{drug.compound}</h4>
                              <span className="text-[9px] font-bold uppercase px-2 py-0.5 rounded-md"
                                style={{ background: tierColors.D.bg, border: `1px solid ${tierColors.D.border}`, color: tierColors.D.text }}>
                                Tier D
                              </span>
                            </div>
                            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{drug.reason}</p>
                            {getRealPaperUrls(drug.pubmed_urls).length > 0 && (
                              <div className="mt-2 space-y-1">
                                {getRealPaperUrls(drug.pubmed_urls).map((url, j) => (
                                  <a key={j} href={url} target="_blank" rel="noreferrer"
                                    className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--accent-primary)', opacity: 0.7 }}>
                                    <BookOpen size={10} /><span>Paper {j + 1}</span><ExternalLink size={9} />
                                  </a>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ──── SECTION: Rejected ──── */}
            {results.rejected?.length > 0 && (
              <div className="glass-card overflow-hidden">
                <button onClick={() => setShowRejected(!showRejected)} className="expand-btn">
                  <div className="flex items-center gap-2.5">
                    <XCircle size={15} style={{ color: '#f87171' }} />
                    <span>Rejected Candidates ({results.rejected.length})</span>
                  </div>
                  {showRejected ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {showRejected && (
                  <>
                    <hr className="section-divider" />
                    <div className="p-6 space-y-3">
                      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                        These compounds did not pass pharmacological safety filtering. Click any drug to view the source literature.
                      </p>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {results.rejected.map((drug, i) => (
                          <div key={i} className="candidate-mini" style={{ borderColor: 'rgba(248, 113, 113, 0.1)' }}>
                            <h4 className="text-sm font-bold mb-2" style={{ color: 'var(--text-secondary)' }}>{drug.compound}</h4>
                            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{drug.reason}</p>
                            {getRealPaperUrls(drug.pubmed_urls).length > 0 && (
                              <div className="mt-2.5 space-y-1">
                                {getRealPaperUrls(drug.pubmed_urls).map((url, j) => (
                                  <a key={j} href={url} target="_blank" rel="noreferrer"
                                    className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--accent-primary)', opacity: 0.7 }}>
                                    <BookOpen size={10} /><span>Paper {j + 1}</span><ExternalLink size={9} />
                                  </a>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ──── SECTION: Other Candidates (unclassified) ──── */}
            {results.other_candidates?.length > 0 && (
              <div className="glass-card overflow-hidden">
                <button onClick={() => setShowOtherCandidates(!showOtherCandidates)} className="expand-btn">
                  <div className="flex items-center gap-2.5">
                    <FlaskConical size={15} style={{ color: 'var(--text-muted)' }} />
                    <span>Other Candidates — Not Classified ({results.other_candidates.length})</span>
                  </div>
                  {showOtherCandidates ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {showOtherCandidates && (
                  <>
                    <hr className="section-divider" />
                    <div className="p-6 space-y-3">
                      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                        These drugs from the prediction file were not individually evaluated by the LLM.
                      </p>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {results.other_candidates.map((drug, i) => (
                          <div key={i} className="candidate-mini">
                            <h4 className="text-sm font-bold mb-2" style={{ color: 'var(--text-secondary)' }}>{drug.compound}</h4>
                            <div className="space-y-1">
                              {getRealPaperUrls(drug.pubmed_urls).map((url, j) => (
                                <a key={j} href={url} target="_blank" rel="noreferrer"
                                  className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--accent-primary)', opacity: 0.7 }}>
                                  <BookOpen size={10} /><span>Paper {j + 1}</span><ExternalLink size={9} />
                                </a>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Footer Note */}
            <p className="text-center text-xs" style={{ color: 'var(--text-muted)', opacity: 0.4 }}>
              Results are AI-generated from PubMed literature and should be validated by domain experts before clinical use.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
