import { useState, useEffect } from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';
import {
    LayoutDashboard,
    FolderTree,
    PlayCircle,
    Settings,
    LogOut,
    Menu,
    X,
    User,
    Users,
    Bell,
    ChevronDown,
    Layers,
    Plus,
    UserCog
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { getProjects } from '@/lib/api';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useQuery } from '@tanstack/react-query';

const navigation = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'Test Suites', href: '/suites', icon: FolderTree },
    { name: 'Test Runs', href: '/runs', icon: PlayCircle },
    { name: 'Users', href: '/users', icon: User },
    { name: 'Tenant Admin', href: '/admin/users', icon: UserCog },
    { name: 'Organization', href: '/organization', icon: Users },
    { name: 'Settings', href: '/settings', icon: Settings },
];

export default function DashboardLayout() {
    const { user, logout } = useAuth();
    const [sidebarOpen, setSidebarOpen] = useState(true);
    const location = useLocation();

    const [selectedProjectId, setSelectedProjectId] = useState<number | null>(() => {
        const saved = localStorage.getItem('activeProjectId');
        return saved ? parseInt(saved) : null;
    });

    const { data: projects } = useQuery({
        queryKey: ['projects'],
        queryFn: () => getProjects()
    });

    const activeProject = projects?.find(p => p.id === selectedProjectId);

    useEffect(() => {
        if (projects && projects.length > 0) {
            // Check if current selectedProjectId is still valid
            if (selectedProjectId && !projects.find(p => p.id === selectedProjectId)) {
                setSelectedProjectId(projects[0].id);
                localStorage.setItem('activeProjectId', projects[0].id.toString());
                window.dispatchEvent(new Event('projectChanged'));
            } else if (!selectedProjectId) {
                setSelectedProjectId(projects[0].id);
                localStorage.setItem('activeProjectId', projects[0].id.toString());
                window.dispatchEvent(new Event('projectChanged'));
            } else {
                localStorage.setItem('activeProjectId', selectedProjectId.toString());
            }
        }
    }, [selectedProjectId, projects]);

    const handleProjectSelect = (id: number) => {
        setSelectedProjectId(id);
        // Refresh page or trigger state update in children
        window.dispatchEvent(new Event('projectChanged'));
    };

    return (
        <div className="min-h-screen bg-background">
            {/* Sidebar */}
            <aside
                className={`fixed inset-y-0 left-0 z-50 w-64 bg-card border-r border-border transform transition-transform duration-200 ease-in-out ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'
                    }`}
            >
                <div className="flex flex-col h-full">
                    {/* Logo */}
                    <div className="flex items-center justify-between h-16 px-6 border-b border-border">
                        <h1 className="text-xl font-bold text-foreground">QI Platform</h1>
                        <button
                            onClick={() => setSidebarOpen(false)}
                            className="lg:hidden text-muted-foreground hover:text-foreground"
                        >
                            <X className="h-6 w-6" />
                        </button>
                    </div>

                    {/* Navigation */}
                    <nav className="flex-1 px-4 py-6 space-y-1 overflow-y-auto">
                        {navigation.map((item) => {
                            const isActive = location.pathname === item.href;
                            return (
                                <Link
                                    key={item.name}
                                    to={item.href}
                                    className={`flex items-center px-4 py-3 text-sm font-medium rounded-lg transition-colors ${isActive
                                        ? 'bg-primary text-primary-foreground'
                                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                                        }`}
                                >
                                    <item.icon className="h-5 w-5 mr-3" />
                                    {item.name}
                                </Link>
                            );
                        })}
                    </nav>

                    {/* User section */}
                    <div className="p-4 border-t border-border">
                        <div className="flex items-center space-x-3 px-4 py-3 rounded-lg hover:bg-accent hover:text-accent-foreground cursor-pointer transition-colors">
                            <div className="flex-shrink-0">
                                <div className="h-10 w-10 rounded-full bg-primary flex items-center justify-center">
                                    <User className="h-6 w-6 text-primary-foreground" />
                                </div>
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium text-foreground truncate">{user?.full_name || 'User'}</p>
                                <p className="text-xs text-muted-foreground truncate">{user?.email}</p>
                            </div>
                        </div>
                        <Button
                            variant="ghost"
                            className="w-full justify-start mt-2 text-red-600 hover:text-red-700 hover:bg-red-50"
                            onClick={logout}
                        >
                            <LogOut className="h-4 w-4 mr-2" />
                            Logout
                        </Button>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <div className={`transition-all duration-200 ${sidebarOpen ? 'lg:pl-64' : ''}`}>
                {/* Top navbar */}
                <header className="sticky top-0 z-40 bg-card border-b border-border">
                    <div className="flex items-center justify-between h-16 px-6">
                        <div className="flex items-center space-x-4">
                            <button
                                onClick={() => setSidebarOpen(!sidebarOpen)}
                                className="text-muted-foreground hover:text-foreground"
                            >
                                <Menu className="h-6 w-6" />
                            </button>

                            {/* Project Selector */}
                            <div className="h-8 w-px bg-border mx-2 hidden sm:block" />
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" className="flex items-center space-x-2 px-3">
                                        <Layers className="h-4 w-4 text-primary" />
                                        <span className="font-semibold text-sm max-w-[150px] truncate">
                                            {activeProject?.name || 'Select Project'}
                                        </span>
                                        <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="start" className="w-56">
                                    <DropdownMenuLabel>Switch Project</DropdownMenuLabel>
                                    <DropdownMenuSeparator />
                                    {projects?.map((p) => (
                                        <DropdownMenuItem
                                            key={p.id}
                                            onClick={() => handleProjectSelect(p.id)}
                                            className={p.id === selectedProjectId ? "bg-accent" : ""}
                                        >
                                            <Layers className="h-4 w-4 mr-2 text-muted-foreground" />
                                            <span className="truncate">{p.name}</span>
                                        </DropdownMenuItem>
                                    ))}
                                    {(!projects || projects.length === 0) && (
                                        <DropdownMenuItem disabled>
                                            No projects found
                                        </DropdownMenuItem>
                                    )}
                                    <DropdownMenuSeparator />
                                    <Link to="/organization">
                                        <DropdownMenuItem>
                                            <Plus className="h-4 w-4 mr-2" />
                                            Manage Projects
                                        </DropdownMenuItem>
                                    </Link>
                                </DropdownMenuContent>
                            </DropdownMenu>
                        </div>

                        <div className="flex items-center space-x-4">
                            <button className="relative text-muted-foreground hover:text-foreground">
                                <Bell className="h-6 w-6" />
                                <span className="absolute top-0 right-0 block h-2 w-2 rounded-full bg-red-500 ring-2 ring-background" />
                            </button>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={logout}
                                className="text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                                title="Logout"
                            >
                                <LogOut className="h-5 w-5" />
                            </Button>
                        </div>
                    </div>
                </header>

                {/* Page content */}
                <main className="p-6">
                    <Outlet />
                </main>
            </div>

            {/* Mobile sidebar overlay */}
            {sidebarOpen && (
                <div
                    className="fixed inset-0 z-40 bg-gray-600 bg-opacity-75 lg:hidden"
                    onClick={() => setSidebarOpen(false)}
                />
            )}
        </div>
    );
}
