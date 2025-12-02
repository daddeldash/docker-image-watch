#!/usr/bin/env python3
"""
Docker Image Watch - Automatic container update checker and updater.
Monitors running containers for image updates and applies them automatically.
"""

import os
import sys
import time
import json
import logging
import signal
import socket
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import docker
from docker.types import Mount
from docker.errors import APIError, ImageNotFound, NotFound
from croniter import croniter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class ContainerReport:
    """Report for a single container check."""
    name: str
    image: str
    status: str  # 'updated', 'up-to-date', 'skipped', 'error'
    message: str = ""


@dataclass 
class UpdateCycleReport:
    """Report for a complete update cycle."""
    timestamp: str
    hostname: str
    duration_seconds: float
    containers_checked: int
    containers_updated: int
    containers_skipped: int
    containers_failed: int
    images_cleaned: int
    space_reclaimed_mb: float
    container_reports: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    
    def to_markdown(self) -> str:
        """Convert report to a formatted Markdown message."""
        # Status emoji
        if self.containers_failed > 0:
            status_emoji = "⚠️"
            status_text = "Completed with errors"
        elif self.containers_updated > 0:
            status_emoji = "✅"
            status_text = "Updates applied"
        else:
            status_emoji = "✓"
            status_text = "No updates needed"
        
        lines = [
            f"## {status_emoji} Docker Image Watch Report",
            "",
            f"**Host:** `{self.hostname}`",
            f"**Time:** {self.timestamp}",
            f"**Duration:** {self.duration_seconds:.1f}s",
            "",
            "### Summary",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Status | {status_text} |",
            f"| Containers checked | {self.containers_checked} |",
            f"| Updated | {self.containers_updated} |",
            f"| Skipped | {self.containers_skipped} |",
            f"| Failed | {self.containers_failed} |",
            f"| Images cleaned | {self.images_cleaned} |",
            f"| Space reclaimed | {self.space_reclaimed_mb:.2f} MB |",
        ]
        
        # Container details
        if self.container_reports:
            lines.extend(["", "### Container Details", ""])
            
            # Group by status
            updated = [c for c in self.container_reports if c.status == 'updated']
            pending_restart = [c for c in self.container_reports if c.status == 'pending-restart']
            up_to_date = [c for c in self.container_reports if c.status == 'up-to-date']
            skipped = [c for c in self.container_reports if c.status == 'skipped']
            errors = [c for c in self.container_reports if c.status == 'error']
            
            if updated:
                lines.append("**🔄 Updated:**")
                for c in updated:
                    lines.append(f"- `{c.name}` ({c.image})")
                lines.append("")
            
            if pending_restart:
                lines.append("**🔄 Self-Update (restarting):**")
                for c in pending_restart:
                    lines.append(f"- `{c.name}` ({c.image})")
                lines.append("")
            
            if errors:
                lines.append("**❌ Errors:**")
                for c in errors:
                    lines.append(f"- `{c.name}`: {c.message}")
                lines.append("")
            
            if skipped:
                lines.append("**⏭️ Skipped:**")
                for c in skipped:
                    reason = f" - {c.message}" if c.message else ""
                    lines.append(f"- `{c.name}`{reason}")
                lines.append("")
            
            if up_to_date:
                lines.append("**✓ Up to date:**")
                for c in up_to_date:
                    lines.append(f"- `{c.name}` ({c.image})")
                lines.append("")
        
        # Global errors
        if self.errors:
            lines.extend(["", "### Errors", ""])
            for error in self.errors:
                lines.append(f"- {error}")
        
        return "\n".join(lines)
    
    def to_slack_blocks(self) -> dict:
        """Convert report to Slack Block Kit format."""
        # Status emoji and color
        if self.containers_failed > 0:
            status_emoji = "⚠️"
            status_text = "Completed with errors"
            color = "warning"
        elif self.containers_updated > 0:
            status_emoji = "✅"
            status_text = "Updates applied"
            color = "good"
        else:
            status_emoji = "✓"
            status_text = "No updates needed"
            color = "#36a64f"
        
        # Build summary text
        summary_parts = []
        if self.containers_updated > 0:
            summary_parts.append(f"{self.containers_updated} updated")
        if self.containers_skipped > 0:
            summary_parts.append(f"{self.containers_skipped} skipped")
        if self.containers_failed > 0:
            summary_parts.append(f"{self.containers_failed} failed")
        summary = ", ".join(summary_parts) if summary_parts else "All containers checked"
        
        # Container lists
        updated = [c for c in self.container_reports if c.status == 'updated']
        errors_list = [c for c in self.container_reports if c.status == 'error']
        
        fields = [
            {"title": "Host", "value": self.hostname, "short": True},
            {"title": "Duration", "value": f"{self.duration_seconds:.1f}s", "short": True},
            {"title": "Containers", "value": f"{self.containers_checked} checked", "short": True},
            {"title": "Cleanup", "value": f"{self.space_reclaimed_mb:.2f} MB freed", "short": True},
        ]
        
        if updated:
            fields.append({
                "title": "🔄 Updated",
                "value": "\n".join([f"`{c.name}`" for c in updated]),
                "short": False
            })
        
        if errors_list:
            fields.append({
                "title": "❌ Errors", 
                "value": "\n".join([f"`{c.name}`: {c.message}" for c in errors_list]),
                "short": False
            })
        
        return {
            "attachments": [{
                "color": color,
                "title": f"{status_emoji} Docker Image Watch - {status_text}",
                "text": summary,
                "fields": fields,
                "footer": "Docker Image Watch",
                "ts": int(datetime.now().timestamp())
            }]
        }
    
    def to_discord(self) -> dict:
        """Convert report to Discord webhook format."""
        # Status emoji and color
        if self.containers_failed > 0:
            color = 0xFFA500  # Orange
            status_text = "⚠️ Completed with errors"
        elif self.containers_updated > 0:
            color = 0x00FF00  # Green
            status_text = "✅ Updates applied"
        else:
            color = 0x36A64F  # Dark green
            status_text = "✓ No updates needed"
        
        # Build fields
        fields = [
            {"name": "Host", "value": f"`{self.hostname}`", "inline": True},
            {"name": "Duration", "value": f"{self.duration_seconds:.1f}s", "inline": True},
            {"name": "Containers", "value": str(self.containers_checked), "inline": True},
        ]
        
        # Updated containers
        updated = [c for c in self.container_reports if c.status == 'updated']
        if updated:
            fields.append({
                "name": "🔄 Updated",
                "value": "\n".join([f"`{c.name}`" for c in updated[:10]]),
                "inline": False
            })
        
        # Errors
        errors_list = [c for c in self.container_reports if c.status == 'error']
        if errors_list:
            fields.append({
                "name": "❌ Errors",
                "value": "\n".join([f"`{c.name}`: {c.message[:50]}" for c in errors_list[:5]]),
                "inline": False
            })
        
        # Cleanup info
        if self.images_cleaned > 0 or self.space_reclaimed_mb > 0:
            fields.append({
                "name": "🧹 Cleanup",
                "value": f"{self.images_cleaned} images, {self.space_reclaimed_mb:.2f} MB freed",
                "inline": False
            })
        
        return {
            "embeds": [{
                "title": f"Docker Image Watch - {status_text}",
                "color": color,
                "fields": fields,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "footer": {"text": "Docker Image Watch"}
            }]
        }


class DockerImageWatch:
    """Monitors and updates Docker containers when new images are available."""
    
    def __init__(self):
        self.client = docker.DockerClient(base_url='unix:///var/run/docker.sock')
        self.api_client = docker.APIClient(base_url='unix:///var/run/docker.sock')
        self.running = True
        
        # Webhook configuration
        self.webhook_url = os.environ.get('WEBHOOK_URL', '')
        self.webhook_format = os.environ.get('WEBHOOK_FORMAT', 'auto').lower()
        # Send webhook only on specific conditions
        self.webhook_on_update = os.environ.get('WEBHOOK_ON_UPDATE', 'true').lower() == 'true'
        self.webhook_on_error = os.environ.get('WEBHOOK_ON_ERROR', 'true').lower() == 'true'
        self.webhook_always = os.environ.get('WEBHOOK_ALWAYS', 'false').lower() == 'true'
        
        # Get hostname for reports (container ID in Docker)
        self.hostname = os.environ.get('HOSTNAME', socket.gethostname())
        
        # Determine our own container name for self-update handling
        self.self_container_name = self._get_self_container_name()
        self.self_container_image = None
        self.self_update_available = False
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _get_self_container_name(self) -> Optional[str]:
        """Determine the name of our own container."""
        # Method 1: Check HOSTNAME which is usually the container ID
        hostname = os.environ.get('HOSTNAME', '')
        if hostname:
            try:
                # Try to find container by ID (hostname is usually container ID)
                container = self.client.containers.get(hostname)
                logger.info(f"Self-detection: Running as container '{container.name}'")
                return container.name
            except (NotFound, APIError):
                pass
        
        # Method 2: Check for explicit environment variable
        explicit_name = os.environ.get('CONTAINER_NAME', '')
        if explicit_name:
            logger.info(f"Self-detection: Using explicit CONTAINER_NAME='{explicit_name}'")
            return explicit_name
        
        # Method 3: Try to find by matching hostname in container info
        try:
            for container in self.client.containers.list():
                try:
                    info = self.api_client.inspect_container(container.id)
                    config = info.get('Config', {})
                    if config.get('Hostname') == hostname:
                        logger.info(f"Self-detection: Found self as container '{container.name}'")
                        return container.name
                except APIError:
                    continue
        except APIError:
            pass
        
        logger.warning("Self-detection: Could not determine own container name")
        return None
    
    def _is_self_container(self, container) -> bool:
        """Check if the given container is ourselves."""
        if self.self_container_name:
            return container.name == self.self_container_name
        # Fallback: check by container ID matching hostname
        return container.id.startswith(self.hostname) or container.short_id == self.hostname[:12]
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def _detect_webhook_format(self, url: str) -> str:
        """Auto-detect webhook format based on URL."""
        url_lower = url.lower()
        if 'discord.com/api/webhooks' in url_lower or 'discordapp.com/api/webhooks' in url_lower:
            return 'discord'
        elif 'hooks.slack.com' in url_lower:
            return 'slack'
        elif 'api.telegram.org' in url_lower:
            return 'telegram'
        else:
            # Default to generic JSON with markdown
            return 'generic'
    
    def send_webhook(self, report: UpdateCycleReport) -> bool:
        """Send update report to configured webhook."""
        if not self.webhook_url:
            return False
        
        # Check if we should send based on conditions
        has_updates = report.containers_updated > 0
        has_errors = report.containers_failed > 0 or len(report.errors) > 0
        
        if not self.webhook_always:
            if not (self.webhook_on_update and has_updates) and not (self.webhook_on_error and has_errors):
                logger.debug("Webhook skipped: no updates or errors to report")
                return False
        
        try:
            # Detect format if set to auto
            webhook_format = self.webhook_format
            if webhook_format == 'auto':
                webhook_format = self._detect_webhook_format(self.webhook_url)
            
            # Prepare payload based on format
            if webhook_format == 'discord':
                payload = report.to_discord()
            elif webhook_format == 'slack':
                payload = report.to_slack_blocks()
            elif webhook_format == 'telegram':
                # Telegram Bot API format
                # URL should be: https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>
                payload = {
                    "text": report.to_markdown(),
                    "parse_mode": "Markdown"
                }
            elif webhook_format == 'markdown':
                # Generic webhook with just markdown text
                payload = {
                    "text": report.to_markdown(),
                    "content": report.to_markdown(),  # Discord raw format
                    "message": report.to_markdown()   # Generic
                }
            elif webhook_format == 'json':
                # Raw JSON report
                payload = asdict(report)
            else:
                # Generic format - try multiple common field names
                payload = {
                    "text": report.to_markdown(),
                    "content": report.to_markdown(),
                    "message": report.to_markdown(),
                    "body": report.to_markdown()
                }
            
            # Send the webhook
            data = json.dumps(payload).encode('utf-8')
            request = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'Docker-Image-Watch/1.0'
                },
                method='POST'
            )
            
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                if status < 300:
                    logger.info(f"Webhook sent successfully (status: {status})")
                    return True
                else:
                    logger.warning(f"Webhook returned status {status}")
                    return False
                    
        except urllib.error.HTTPError as e:
            logger.error(f"Webhook HTTP error: {e.code} - {e.reason}")
            return False
        except urllib.error.URLError as e:
            logger.error(f"Webhook URL error: {e.reason}")
            return False
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return False
    
    def get_image_digest(self, image_name: str) -> Optional[str]:
        """Get the current digest of a local image."""
        try:
            image = self.client.images.get(image_name)
            # Get RepoDigests if available
            repo_digests = image.attrs.get('RepoDigests', [])
            if repo_digests:
                return repo_digests[0]
            # Fallback to image ID
            return image.id
        except ImageNotFound:
            return None
        except APIError as e:
            logger.error(f"Error getting image digest for {image_name}: {e}")
            return None
    
    def pull_image(self, image_name: str) -> tuple[bool, bool]:
        """
        Pull the latest version of an image.
        
        Returns:
            tuple: (success: bool, is_local_only: bool)
                - success: True if image was pulled successfully
                - is_local_only: True if the image doesn't exist in any registry
        """
        try:
            # Parse image name and tag
            if ':' in image_name:
                repo, tag = image_name.rsplit(':', 1)
            else:
                repo, tag = image_name, 'latest'
            
            logger.info(f"Pulling image: {repo}:{tag}")
            
            # Pull with progress output
            for line in self.api_client.pull(repo, tag=tag, stream=True, decode=True):
                if 'status' in line:
                    status = line['status']
                    progress = line.get('progress', '')
                    if progress:
                        logger.debug(f"  {status}: {progress}")
                    elif 'id' in line:
                        logger.debug(f"  {line['id']}: {status}")
            
            logger.info(f"Successfully pulled image: {repo}:{tag}")
            return True, False
            
        except APIError as e:
            error_msg = str(e)
            # Check if this is a "repository does not exist" error
            if 'repository does not exist' in error_msg or 'pull access denied' in error_msg:
                logger.info(f"Skipping {image_name}: not found in registry (local-only image)")
                return False, True
            elif 'not found' in error_msg.lower():
                logger.info(f"Skipping {image_name}: image not found in registry")
                return False, True
            else:
                logger.error(f"Failed to pull image {image_name}: {e}")
                return False, False
    
    def get_image_name(self, container) -> Optional[str]:
        """Extract the image name from a container."""
        # First try to get tagged image name
        if container.image.tags:
            return container.image.tags[0]
        
        # Try to get from container config
        config_image = container.attrs.get('Config', {}).get('Image', '')
        if config_image and not config_image.startswith('sha256:'):
            return config_image
        
        return None
    
    def is_local_only_image(self, image_name: str) -> bool:
        """Check if an image is local-only (not from a registry)."""
        if not image_name:
            return True
        
        # SHA256 references are local
        if image_name.startswith('sha256:'):
            return True
        
        try:
            image = self.client.images.get(image_name)
            repo_digests = image.attrs.get('RepoDigests', [])
            
            # If no repo digests at all, it's definitely a locally built image
            if not repo_digests:
                logger.debug(f"Image {image_name} has no repo digests - local only")
                return True
            
            # Check each repo digest for a registry host
            for digest in repo_digests:
                digest_repo = digest.split('@')[0]
                
                # Check if digest has a registry prefix (contains / and has . or : in first part)
                if '/' in digest_repo:
                    first_segment = digest_repo.split('/')[0]
                    # Registry hosts have dots (docker.io, ghcr.io) or ports (localhost:5000)
                    if '.' in first_segment or ':' in first_segment:
                        logger.debug(f"Image {image_name} has registry in digest: {digest_repo}")
                        return False
                else:
                    # No slash in digest repo - could be official Docker Hub image
                    # Official images like "nginx", "python", "alpine" come from Docker Hub
                    # Their digests are stored as "imagename@sha256:..." without the registry prefix
                    # We need to check if this matches a known Docker Hub official image pattern
                    
                    # If the digest repo matches the image name (without tag), it's from Docker Hub
                    image_base = image_name.split(':')[0]
                    if digest_repo == image_base or digest_repo == image_base.split('/')[-1]:
                        # This looks like an official Docker Hub image
                        logger.debug(f"Image {image_name} appears to be official Docker Hub image")
                        return False
            
            # No matching registry patterns found - likely a locally built image
            logger.debug(f"Image {image_name} has only local-style digests - local only")
            return True
            
        except (ImageNotFound, APIError):
            # Can't find image locally - it might be pullable
            # Check if it looks like a registry/official image name
            name_part = image_name.split(':')[0]
            
            if '/' in name_part:
                first_segment = name_part.split('/')[0]
                # Has explicit registry prefix
                if '.' in first_segment or ':' in first_segment:
                    return False
                # Has namespace but no registry (e.g., "myuser/myimage")
                # Could be Docker Hub
                return False
            else:
                # No slash - could be official Docker Hub image (python, nginx, etc.)
                # or a local-only image (myimage)
                # Assume it might be official and try to pull
                return False
    
    def recreate_container(self, container) -> bool:
        """Recreate a container with the updated image."""
        try:
            container_name = container.name
            logger.info(f"Recreating container: {container_name}")
            
            # Get container configuration
            config = container.attrs
            host_config = config.get('HostConfig', {})
            network_settings = config.get('NetworkingConfig', {})
            
            # Get the image
            image = config['Config']['Image']
            
            # Prepare container configuration
            container_config = {
                'image': image,
                'name': container_name,
                'detach': True,
            }
            
            # Copy essential configuration
            if config['Config'].get('Env'):
                container_config['environment'] = config['Config']['Env']
            
            if config['Config'].get('Cmd'):
                container_config['command'] = config['Config']['Cmd']
            
            if config['Config'].get('Entrypoint'):
                container_config['entrypoint'] = config['Config']['Entrypoint']
            
            if config['Config'].get('WorkingDir'):
                container_config['working_dir'] = config['Config']['WorkingDir']
            
            if config['Config'].get('User'):
                container_config['user'] = config['Config']['User']
            
            if config['Config'].get('Labels'):
                container_config['labels'] = config['Config']['Labels']
            
            # Port bindings
            if host_config.get('PortBindings'):
                container_config['ports'] = host_config['PortBindings']
            
            # Volume mounts
            if host_config.get('Binds'):
                container_config['volumes'] = {}
                binds = []
                for bind in host_config['Binds']:
                    parts = bind.split(':')
                    if len(parts) >= 2:
                        binds.append(bind)
                if binds:
                    container_config['volumes'] = binds
            
            # Mounts (newer format)
            if host_config.get('Mounts'):
                container_config['mounts'] = []
                for mount in host_config['Mounts']:
                    container_config['mounts'].append(Mount(
                        target=mount['Target'],
                        source=mount['Source'],
                        type=mount['Type'],
                        read_only=mount.get('ReadOnly', False)
                    ))
            
            # Network mode
            if host_config.get('NetworkMode'):
                container_config['network_mode'] = host_config['NetworkMode']
            
            # Restart policy
            if host_config.get('RestartPolicy'):
                policy = host_config['RestartPolicy']
                container_config['restart_policy'] = {
                    'Name': policy.get('Name', 'no'),
                    'MaximumRetryCount': policy.get('MaximumRetryCount', 0)
                }
            
            # Privileged mode
            if host_config.get('Privileged'):
                container_config['privileged'] = host_config['Privileged']
            
            # Capabilities
            if host_config.get('CapAdd'):
                container_config['cap_add'] = host_config['CapAdd']
            if host_config.get('CapDrop'):
                container_config['cap_drop'] = host_config['CapDrop']
            
            # Resource limits
            if host_config.get('Memory'):
                container_config['mem_limit'] = host_config['Memory']
            if host_config.get('NanoCpus'):
                container_config['nano_cpus'] = host_config['NanoCpus']
            
            # Stop the old container
            logger.info(f"Stopping container: {container_name}")
            container.stop(timeout=30)
            
            # Remove the old container
            logger.info(f"Removing old container: {container_name}")
            container.remove()
            
            # Create and start the new container
            logger.info(f"Creating new container: {container_name}")
            new_container = self.client.containers.run(**container_config)
            
            logger.info(f"Successfully recreated container: {container_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to recreate container {container.name}: {e}")
            return False
    
    def cleanup_unused_images(self) -> tuple[int, float]:
        """
        Remove all unused (dangling) images.
        
        Returns:
            tuple: (images_deleted: int, space_reclaimed_mb: float)
        """
        total_deleted = 0
        total_space = 0.0
        
        try:
            logger.info("Cleaning up unused images...")
            
            # Prune dangling images first
            pruned = self.client.images.prune(filters={'dangling': True})
            
            deleted_count = len(pruned.get('ImagesDeleted', []) or [])
            space_reclaimed = pruned.get('SpaceReclaimed', 0)
            total_deleted += deleted_count
            total_space += space_reclaimed
            
            if deleted_count > 0:
                logger.info(f"Removed {deleted_count} dangling images, reclaimed {space_reclaimed / (1024**2):.2f} MB")
            
            # Also prune unused images (not just dangling)
            pruned_all = self.client.images.prune()
            
            deleted_count_all = len(pruned_all.get('ImagesDeleted', []) or [])
            space_reclaimed_all = pruned_all.get('SpaceReclaimed', 0)
            total_deleted += deleted_count_all
            total_space += space_reclaimed_all
            
            if deleted_count_all > 0:
                logger.info(f"Removed {deleted_count_all} unused images, reclaimed {space_reclaimed_all / (1024**2):.2f} MB")
            
            if deleted_count == 0 and deleted_count_all == 0:
                logger.info("No unused images to remove")
                
        except APIError as e:
            logger.error(f"Failed to cleanup images: {e}")
        
        return total_deleted, total_space / (1024**2)
    
    def run_update_cycle(self):
        """Run a complete update cycle for all running containers."""
        start_time = datetime.now()
        
        # Initialize report
        report = UpdateCycleReport(
            timestamp=start_time.strftime('%Y-%m-%d %H:%M:%S'),
            hostname=self.hostname,
            duration_seconds=0,
            containers_checked=0,
            containers_updated=0,
            containers_skipped=0,
            containers_failed=0,
            images_cleaned=0,
            space_reclaimed_mb=0,
            container_reports=[],
            errors=[]
        )
        
        logger.info("=" * 60)
        logger.info("Starting update cycle...")
        logger.info("=" * 60)
        
        try:
            # Get all running containers
            containers = self.client.containers.list()
            
            if not containers:
                logger.info("No running containers found")
                report.duration_seconds = (datetime.now() - start_time).total_seconds()
                self.send_webhook(report)
                return
            
            logger.info(f"Found {len(containers)} running container(s)")
            
            # Reset self-update tracking
            self.self_update_available = False
            self.self_container_image = None
            
            for container in containers:
                # Check if this is our own container
                if self._is_self_container(container):
                    # Handle self-update separately
                    image_name = self.get_image_name(container) or "unknown"
                    logger.info(f"Checking self-container: {container.name}")
                    
                    # Check for update but don't recreate
                    update_result = self.check_for_update_with_status(container)
                    
                    if update_result == 'update_available':
                        self.self_update_available = True
                        self.self_container_image = image_name
                        logger.info(f"Self-update available for {container.name} - will restart after cycle completes")
                        report.containers_checked += 1
                        report.container_reports.append(ContainerReport(
                            name=container.name,
                            image=image_name,
                            status='pending-restart',
                            message='self-update: restart required'
                        ))
                    elif update_result == 'up_to_date':
                        report.containers_checked += 1
                        report.container_reports.append(ContainerReport(
                            name=container.name,
                            image=image_name,
                            status='up-to-date',
                            message='(self)'
                        ))
                    elif update_result == 'skipped':
                        report.containers_skipped += 1
                        report.container_reports.append(ContainerReport(
                            name=container.name,
                            image=image_name,
                            status='skipped',
                            message='local-only image (self)'
                        ))
                    continue
                
                report.containers_checked += 1
                image_name = self.get_image_name(container) or "unknown"
                
                # Check for label to exclude from updates
                labels = container.labels
                if labels.get('docker-image-watch.disable', '').lower() == 'true':
                    logger.info(f"Skipping container (disabled by label): {container.name}")
                    report.containers_skipped += 1
                    report.container_reports.append(ContainerReport(
                        name=container.name,
                        image=image_name,
                        status='skipped',
                        message='disabled by label'
                    ))
                    continue
                
                logger.info(f"Checking container: {container.name}")
                
                # Check for update
                update_result = self.check_for_update_with_status(container)
                
                if update_result == 'skipped':
                    report.containers_skipped += 1
                    report.container_reports.append(ContainerReport(
                        name=container.name,
                        image=image_name,
                        status='skipped',
                        message='local-only image'
                    ))
                elif update_result == 'update_available':
                    # Try to recreate container
                    if self.recreate_container(container):
                        report.containers_updated += 1
                        report.container_reports.append(ContainerReport(
                            name=container.name,
                            image=image_name,
                            status='updated',
                            message='successfully updated'
                        ))
                    else:
                        report.containers_failed += 1
                        report.container_reports.append(ContainerReport(
                            name=container.name,
                            image=image_name,
                            status='error',
                            message='failed to recreate container'
                        ))
                elif update_result == 'up_to_date':
                    report.container_reports.append(ContainerReport(
                        name=container.name,
                        image=image_name,
                        status='up-to-date',
                        message=''
                    ))
                elif update_result == 'error':
                    report.containers_failed += 1
                    report.container_reports.append(ContainerReport(
                        name=container.name,
                        image=image_name,
                        status='error',
                        message='error checking for updates'
                    ))
            
            # Cleanup unused images after updates
            images_cleaned, space_reclaimed = self.cleanup_unused_images()
            report.images_cleaned = images_cleaned
            report.space_reclaimed_mb = space_reclaimed
            
            # Summary
            logger.info("=" * 60)
            if report.containers_updated > 0:
                updated_names = [c.name for c in report.container_reports if c.status == 'updated']
                logger.info(f"Update cycle complete. Updated containers: {', '.join(updated_names)}")
            else:
                logger.info("Update cycle complete. No containers were updated.")
            
            # Handle self-update at the end
            if self.self_update_available:
                logger.info(f"Self-update pending - performing self-restart...")
                report.containers_updated += 1
            
            logger.info("=" * 60)
            
        except APIError as e:
            error_msg = f"Docker API error during update cycle: {e}"
            logger.error(error_msg)
            report.errors.append(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error during update cycle: {e}"
            logger.error(error_msg)
            report.errors.append(error_msg)
        
        # Finalize report
        report.duration_seconds = (datetime.now() - start_time).total_seconds()
        
        # Send webhook
        self.send_webhook(report)
        
        # Perform self-restart AFTER webhook is sent
        if self.self_update_available:
            self._perform_self_restart()
    
    def _perform_self_restart(self):
        """
        Perform a self-restart by stopping our container.
        Docker's restart policy (unless-stopped/always) will restart us with the new image.
        """
        if not self.self_container_name:
            logger.error("Cannot perform self-restart: own container name unknown")
            return
        
        try:
            # Get our own container
            container = self.client.containers.get(self.self_container_name)
            
            logger.info("=" * 60)
            logger.info("SELF-UPDATE: Restarting with new image...")
            logger.info("Container will be restarted by Docker's restart policy")
            logger.info("=" * 60)
            
            # Give a moment for logs to flush
            time.sleep(1)
            
            # Stop ourselves - Docker restart policy will bring us back up with new image
            container.stop(timeout=10)
            
            # This code won't be reached as we're stopped
        except NotFound:
            logger.error(f"Self-restart failed: Container '{self.self_container_name}' not found")
        except APIError as e:
            logger.error(f"Self-restart failed: {e}")
    
    def check_for_update_with_status(self, container) -> str:
        """
        Check if a container's image has an update available.
        
        Returns:
            str: 'update_available', 'up_to_date', 'skipped', or 'error'
        """
        try:
            image_name = self.get_image_name(container)
            
            if not image_name:
                logger.info(f"Skipping {container.name}: no tagged image name (local build)")
                return 'skipped'
            
            # Skip local-only images (not from a registry)
            if self.is_local_only_image(image_name):
                logger.info(f"Skipping {container.name}: local-only image '{image_name}'")
                return 'skipped'
            
            # Get current local digest
            old_digest = self.get_image_digest(image_name)
            
            # Pull latest image
            pull_success, is_local_only = self.pull_image(image_name)
            if not pull_success:
                if is_local_only:
                    return 'skipped'
                return 'error'
            
            # Get new digest
            new_digest = self.get_image_digest(image_name)
            
            # Compare digests
            if old_digest and new_digest and old_digest != new_digest:
                logger.info(f"Update available for {container.name}: {image_name}")
                return 'update_available'
            else:
                logger.info(f"No update available for {container.name}: {image_name}")
                return 'up_to_date'
                
        except Exception as e:
            logger.error(f"Error checking update for container {container.name}: {e}")
            return 'error'
    
    def run(self):
        """Main run loop with cron-based scheduling."""
        # Get cron schedule from environment variable
        cron_schedule = os.environ.get('UPDATE_SCHEDULE', '0 4 * * *')  # Default: 4 AM daily
        
        logger.info("=" * 60)
        logger.info("Docker Image Watch - Starting")
        logger.info(f"Update schedule: {cron_schedule}")
        if self.webhook_url:
            logger.info(f"Webhook: enabled ({self.webhook_format})")
        else:
            logger.info("Webhook: disabled")
        logger.info("=" * 60)
        
        # Validate cron expression
        try:
            cron = croniter(cron_schedule, datetime.now())
        except (KeyError, ValueError) as e:
            logger.error(f"Invalid cron schedule '{cron_schedule}': {e}")
            logger.info("Using default schedule: 0 4 * * *")
            cron_schedule = '0 4 * * *'
            cron = croniter(cron_schedule, datetime.now())
        
        # Check if we should run immediately on startup
        run_on_startup = os.environ.get('RUN_ON_STARTUP', 'false').lower() == 'true'
        
        if run_on_startup:
            logger.info("Running initial update cycle on startup...")
            self.run_update_cycle()
        
        # Main scheduling loop
        while self.running:
            try:
                # Calculate next run time
                next_run = cron.get_next(datetime)
                wait_seconds = (next_run - datetime.now()).total_seconds()
                
                if wait_seconds > 0:
                    logger.info(f"Next update scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    # Sleep in small intervals to allow graceful shutdown
                    while wait_seconds > 0 and self.running:
                        sleep_time = min(wait_seconds, 60)  # Check every minute
                        time.sleep(sleep_time)
                        wait_seconds -= sleep_time
                
                if self.running:
                    self.run_update_cycle()
                    # Update cron iterator for next iteration
                    cron = croniter(cron_schedule, datetime.now())
                    
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(60)  # Wait a minute before retrying
        
        logger.info("Docker Image Watch - Stopped")


def main():
    """Entry point for the application."""
    try:
        watcher = DockerImageWatch()
        watcher.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
