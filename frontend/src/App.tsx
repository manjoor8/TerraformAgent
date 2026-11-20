import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import { AppShell } from "./components/AppShell";
import { LoadingBlock } from "./components/UI";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SubscriptionsPage } from "./pages/SubscriptionsPage";
import { AuditPage } from "./pages/AuditPage";
import { SystemPage } from "./pages/SystemPage";
import { LLMConfigPage } from "./pages/LLMConfigPage";
import { ProjectLayout } from "./pages/ProjectLayout";
import { OverviewTab } from "./pages/project/OverviewTab";
import { ChatTab } from "./pages/project/ChatTab";
import { DocumentsTab } from "./pages/project/DocumentsTab";
import { ArchitectureTab } from "./pages/project/ArchitectureTab";
import { ReviewTab } from "./pages/project/ReviewTab";
import { TerraformTab } from "./pages/project/TerraformTab";
import { ValidationTab } from "./pages/project/ValidationTab";
import { PlanTab } from "./pages/project/PlanTab";
import { DeploymentTab } from "./pages/project/DeploymentTab";
import { AuditTab } from "./pages/project/AuditTab";
import { SettingsTab } from "./pages/project/SettingsTab";

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <LoadingBlock label="Restoring session…" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <Protected>
            <AppShell />
          </Protected>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<OverviewTab />} />
          <Route path="chat" element={<ChatTab />} />
          <Route path="documents" element={<DocumentsTab />} />
          <Route path="architecture" element={<ArchitectureTab />} />
          <Route path="review" element={<ReviewTab />} />
          <Route path="terraform" element={<TerraformTab />} />
          <Route path="validation" element={<ValidationTab />} />
          <Route path="plan" element={<PlanTab />} />
          <Route path="deployment" element={<DeploymentTab />} />
          <Route path="audit" element={<AuditTab />} />
          <Route path="settings" element={<SettingsTab />} />
        </Route>
        <Route path="subscriptions" element={<SubscriptionsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="llm" element={<LLMConfigPage />} />
        <Route path="system" element={<SystemPage />} />
      </Route>
      <Route
        path="*"
        element={
          <div className="grid min-h-screen place-items-center p-6 text-center">
            <div>
              <p className="text-5xl font-semibold text-ink">404</p>
              <p className="mt-2 text-sm text-muted">This page does not exist.</p>
              <a className="btn btn-primary mt-4" href="/">
                Back to dashboard
              </a>
            </div>
          </div>
        }
      />
    </Routes>
  );
}
