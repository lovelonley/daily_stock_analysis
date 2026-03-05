import type React from 'react';
import {BrowserRouter as Router, Routes, Route, NavLink, useLocation, Navigate} from 'react-router-dom';
import { useState, useEffect, useCallback, useRef } from 'react';
import HomePage from './pages/HomePage';
import BacktestPage from './pages/BacktestPage';
import SettingsPage from './pages/SettingsPage';
import LoginPage from './pages/LoginPage';
import NotFoundPage from './pages/NotFoundPage';
import ChatPage from './pages/ChatPage';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { getMonitorStatus, type MonitorStatus } from './api/monitor';
import './App.css';

// 侧边导航图标
const HomeIcon: React.FC<{ active?: boolean }> = ({active}) => (
    <svg className="w-6 h-6" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
    </svg>
);

const BacktestIcon: React.FC<{ active?: boolean }> = ({active}) => (
    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 2 : 1.5}
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/>
    </svg>
);

const SettingsIcon: React.FC<{ active?: boolean }> = ({active}) => (
    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 2 : 1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
    </svg>
);

const ChatIcon: React.FC<{ active?: boolean }> = ({active}) => (
    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 2 : 1.5}
              d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/>
    </svg>
);

const LogoutIcon: React.FC = () => (
    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
    </svg>
);

type DockItem = {
    key: string;
    label: string;
    to: string;
    icon: React.FC<{ active?: boolean }>;
};

const NAV_ITEMS: DockItem[] = [
    {
        key: 'home',
        label: '首页',
        to: '/',
        icon: HomeIcon,
    },
    {
        key: 'chat',
        label: '问股',
        to: '/chat',
        icon: ChatIcon,
    },
    {
        key: 'backtest',
        label: '回测',
        to: '/backtest',
        icon: BacktestIcon,
    },
    {
        key: 'settings',
        label: '设置',
        to: '/settings',
        icon: SettingsIcon,
    },
];

// 盘中监控状态指示
const LEVEL_CONFIG: Record<string, { color: string; label: string; animate: boolean }> = {
    offline: { color: '#606070', label: '离线', animate: false },
    normal:  { color: '#00ff88', label: '正常', animate: false },
    watch:   { color: '#ffaa00', label: '关注', animate: false },
    warning: { color: '#ffaa00', label: '警告', animate: true },
    danger:  { color: '#ff4466', label: '危险', animate: true },
};

const PHASE_LABEL: Record<string, string> = {
    offline: '监控离线',
    pre_market: '盘前',
    live: '盘中监控',
    lunch_break: '午休',
    closed: '已收盘',
};

const useSentinelStatus = () => {
    const [status, setStatus] = useState<MonitorStatus | null>(null);

    const poll = useCallback(async () => {
        try {
            const data = await getMonitorStatus();
            setStatus(data);
        } catch {
            setStatus(null);
        }
    }, []);

    useEffect(() => {
        poll();
        const id = setInterval(poll, 10_000); // 10秒轮询
        return () => clearInterval(id);
    }, [poll]);

    return status;
};

const SentinelDot: React.FC = () => {
    const status = useSentinelStatus();
    const [hover, setHover] = useState(false);
    const [popupPos, setPopupPos] = useState<{top: number; left: number} | null>(null);
    const dotRef = useRef<HTMLDivElement>(null);
    const level = status?.level ?? 'offline';
    const phase = status?.phase ?? 'offline';
    const cfg = LEVEL_CONFIG[level] ?? LEVEL_CONFIG.offline;

    // 构建详情行
    const lines: string[] = [];
    lines.push(`${PHASE_LABEL[phase] ?? phase} · ${cfg.label}`);

    if (status?.snapshot) {
        const s = status.snapshot;
        lines.push(`跌停 ${s.limit_down}  涨停 ${s.limit_up}`);
        lines.push(`中位数 ${(s.median_pct * 100).toFixed(2)}%`);
        lines.push(`沪深300 ${(s.csi300_pct * 100).toFixed(2)}%`);
        lines.push(`下跌占比 ${(s.decline_ratio * 100).toFixed(0)}%`);
    }

    if (status?.reasons && status.reasons.length > 0) {
        lines.push('---');
        status.reasons.forEach(r => lines.push(r));
    }

    // 收盘/盘前 显示摘要
    if ((phase === 'closed' || phase === 'pre_market') && !status?.snapshot) {
        if (status?.date) lines.push(status.date);
        if (status?.peak_limit_down != null) lines.push(`跌停峰值 ${status.peak_limit_down}`);
        if (status?.last_csi300 != null) lines.push(`沪深300 ${(status.last_csi300 * 100).toFixed(2)}%`);
        if (status?.max_level_time) lines.push(`最高告警 ${status.max_level_time}`);
    }

    const handleEnter = () => {
        if (dotRef.current) {
            const rect = dotRef.current.getBoundingClientRect();
            setPopupPos({ top: rect.top + rect.height / 2, left: rect.right + 12 });
        }
        setHover(true);
    };

    return (
        <div
            ref={dotRef}
            className="sentinel-dot-wrapper"
            onMouseEnter={handleEnter}
            onMouseLeave={() => setHover(false)}
        >
            <span
                className={`sentinel-dot${cfg.animate ? ' sentinel-dot--pulse' : ''}`}
                style={{ backgroundColor: cfg.color, boxShadow: `0 0 6px ${cfg.color}` }}
            />
            {hover && popupPos && (
                <div className="sentinel-popup" style={{ top: popupPos.top, left: popupPos.left }}>
                    {lines.map((line, i) =>
                        line === '---'
                            ? <hr key={i} className="sentinel-popup-hr" />
                            : <div key={i} className={i === 0 ? 'sentinel-popup-title' : 'sentinel-popup-line'}>{line}</div>
                    )}
                </div>
            )}
        </div>
    );
};

// Dock 导航栏
const DockNav: React.FC = () => {
    const {authEnabled, logout} = useAuth();
    return (
        <aside className="dock-nav" aria-label="主导航">
            <div className="dock-surface">
                <NavLink to="/" className="dock-logo" title="首页" aria-label="首页">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                              d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
                    </svg>
                </NavLink>

                <SentinelDot />

                <nav className="dock-items" aria-label="页面">
                    {NAV_ITEMS.map((item) => {
                        const Icon = item.icon;
                        return (
                            <NavLink
                                key={item.key}
                                to={item.to}
                                end={item.to === '/'}
                                title={item.label}
                                aria-label={item.label}
                                className={({isActive}) => `dock-item${isActive ? ' is-active' : ''}`}
                            >
                                {({isActive}) => <Icon active={isActive}/>}
                            </NavLink>
                        );
                    })}
                </nav>

                {authEnabled ? (
                    <button
                        type="button"
                        onClick={() => logout()}
                        title="退出登录"
                        aria-label="退出登录"
                        className="dock-item"
                    >
                        <LogoutIcon/>
                    </button>
                ) : null}

                <div className="dock-footer"/>
            </div>
        </aside>
    );
};

const AppContent: React.FC = () => {
    const location = useLocation();
    const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();

    if (isLoading) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-base">
                <div className="w-8 h-8 border-2 border-cyan/20 border-t-cyan rounded-full animate-spin" />
            </div>
        );
    }

    if (loadError) {
        return (
            <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
                <p className="text-sm text-red-400">无法连接到服务器，请检查后端是否正常运行。</p>
                <button
                    type="button"
                    className="btn-primary"
                    onClick={() => void refreshStatus()}
                >
                    重试
                </button>
            </div>
        );
    }

    if (authEnabled && !loggedIn) {
        if (location.pathname === '/login') {
            return <LoginPage />;
        }
        const redirect = encodeURIComponent(location.pathname + location.search);
        return <Navigate to={`/login?redirect=${redirect}`} replace />;
    }

    if (location.pathname === '/login') {
        return <Navigate to="/" replace />;
    }

    return (
        <div className="flex min-h-screen bg-base">
            <DockNav/>
            <main className="flex-1 dock-safe-area">
                <Routes>
                    <Route path="/" element={<HomePage/>}/>
                    <Route path="/chat" element={<ChatPage/>}/>
                    <Route path="/backtest" element={<BacktestPage/>}/>
                    <Route path="/settings" element={<SettingsPage/>}/>
                    <Route path="/login" element={<LoginPage/>}/>
                    <Route path="*" element={<NotFoundPage/>}/>
                </Routes>
            </main>
        </div>
    );
};

const App: React.FC = () => {
    return (
        <Router>
            <AuthProvider>
                <AppContent/>
            </AuthProvider>
        </Router>
    );
};

export default App;
