#!/usr/bin/env python3
"""This module provides the SlurmInstallManager."""

import logging
import os
from pathlib import Path
import subprocess
from time import sleep


from jinja2 import Environment, FileSystemLoader


from ops.framework import (
    Object,
    StoredState,
)
from ops.model import ModelError


logger = logging.getLogger()


class SlurmInstallManager(Object):
    """Slurm installation of lifecycle ops."""

    _store = StoredState()

    _CHARM_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
    _TEMPLATE_DIR = _CHARM_DIR / 'templates'

    _SLURM_CONF_DIR = Path('/etc/slurm')
    _SLURM_USER = "slurm"
    _SLURM_UID = 995
    _SLURM_GROUP = "slurm"
    _SLURM_GID = 995
    _SLURM_TMP_RESOURCE = "/tmp/slurm-resource"

    def __init__(self, charm, key):
        """Determine slurm component and config template from key."""
        super().__init__(charm, key)
        self._store.set_default(slurm_installed=False)
        self._store.set_default(slurm_started=False)
        
        components = [
                'slurmd',
                'slurmctld',
                'slurmrestd',
                'slurmd',
        ]
        
        if key in components:
            self._slurm_component = key
            self._slurm_conf_template_name = 'slurm.conf.tmpl'
            self._slurm_conf_template_location = \
                self._TEMPLATE_DIR / self._slurm_conf_template_name
            self._slurm_conf = self._SLURM_CONF_DIR / 'slurm.conf'
        elif key == "slurmdbd":
            self._slurm_component = key
            self._slurm_conf_template_name = 'slurmdbd.conf.tmpl'
            self._slurm_conf_template_location = \
                self._TEMPLATE_DIR / self._slurm_conf_template_name
            self._slurm_conf = self._SLURM_CONF_DIR / 'slurmdbd.conf'
        
        else:
            raise Exception(f'slurm component {key} not supported')
        
        self._source_systemd_template = \
            self._TEMPLATE_DIR / f'{self._slurm_component}.service'
        self._target_systemd_template = \
            Path(f'/etc/systemd/system/{self._slurm_component}.service')

    @property
    def slurm_installed(self):
        return self._store.slurm_installed

    @property
    def slurm_component_started(self):
        return self._store.slurm_started

    def slurm_systemctl(self, operation):
        """Start systemd services for slurmd."""

        if operation not in ["enable", "start", "stop", "restart"]:
            msg = f"Unsupported systemctl command for {self._slurm_component}"
            raise Exception(msg)

        try:
            subprocess.call([
                "systemctl",
                operation,
                self._slurm_component,
            ])
            # Fix this later
            if operation == "start":
                self._store.slurm_started = True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error copying systemd - {e}")

    def write_config(self, context):
        """Render the context to a template."""

        #ctxt = {}
        template_name = self._slurm_conf_template_name
        source = self._slurm_conf_template_location
        target = self._slurm_conf

        if not type(context) == dict:
            raise TypeError("Incorrect type for config.")
            #ctxt = {**{"hostname": self._hostname}, **context}

        if not source.exists():
            raise FileNotFoundError(
                "The slurm config template cannot be found."
            )

        rendered_template = Environment(
            loader=FileSystemLoader(str(self._TEMPLATE_DIR))
        ).get_template(template_name)

        if target.exists():
            target.unlink()

        target.write_text(rendered_template.render(context))

    def prepare_system_for_slurm(self):
        """Prepare the system for slurm.

        * create slurm user/group
        * create filesystem for slurm
        * provision slurm resource
        """
        self._create_slurm_user_and_group()
        self._prepare_filesystem()

        self._provision_slurm_resource()
        self._set_ld_library_path()

        self._setup_systemd()
        self._store.slurm_installed = True

    def _chown_slurm_user_and_group_recursive(self, slurm_dir):
        """Recursively chown filesystem location to slurm user/slurm group."""
        try:
            subprocess.call([
                "chown",
                "-R",
                f"{self._SLURM_USER}:{self._SLURM_GROUP}",
                slurm_dir,
            ])
        except subprocess.CalledProcessError as e:
            logger.error(f"Error chowning {slurm_dir} - {e}")

    def _create_slurm_user_and_group(self):
        """Create the slurm user and group."""
        try:
            subprocess.call([
                "groupadd",
                "-r",
                f"--gid={self._SLURM_GID}",
                self._SLURM_USER,
            ])
        except subprocess.CalledProcessError as e:
            logger.error(f"Error creating {self._SLURM_GROUP} - {e}")

        try:
            subprocess.call([
                "useradd",
                "-r",
                "-g",
                self._SLURM_GROUP,
                f"--uid={self._SLURM_UID}",
                self._SLURM_USER,
            ])
        except subprocess.CalledProcessError as e:
            logger.error(f"Error creating {self._SLURM_USER} - {e}")

    def _prepare_for_slurmd(self):
        """Create slurmd specific files and dirs."""
        slurmd_dirs = [
            "/var/spool/slurmd",
            "/var/run/slurmd",
            "/var/lib/slurmd",
            "/etc/slurm",
        ]
        for slurmd_dir in slurmd_dirs:
            Path(slurmd_dir).mkdir(parents=True)
            self._chown_slurm_user_and_group_recursive(slurmd_dir)

        slurmd_files = [
            "/var/lib/slurmd/node_state",
            "/var/lib/slurmd/front_end_state",
            "/var/lib/slurmd/job_state",
            "/var/lib/slurmd/resv_state",
            "/var/lib/slurmd/trigger_state",
            "/var/lib/slurmd/assoc_mgr_state",
            "/var/lib/slurmd/assoc_usage",
            "/var/lib/slurmd/qos_usage",
            "/var/lib/slurmd/fed_mgr_state",
        ]
        for slurmd_file in slurmd_files:
            Path(slurmd_file).touch()
        self._chown_slurm_user_and_group_recursive('/var/lib/slurmd')

    def _prepare_filesystem(self):
        """Create the needed system directories needed by slurm."""
        slurm_dirs = [
            "/etc/sysconfig/slurm",
            "/var/log/slurm",
        ]
        for slurm_dir in slurm_dirs:
            Path(slurm_dir).mkdir(parents=True)
            self._chown_slurm_user_and_group_recursive(slurm_dir)

        if self._slurm_component == "slurmd":
            self._prepare_for_slurmd()

    def _provision_slurm_resource(self):
        """Provision the slurm resource."""
        try:
            resource_path = self.model.resources.fetch('slurm')
        except ModelError as e:
            logger.error(
                f"Resource could not be found when executing: {e}",
                exc_info=True,
            )

        try:
            subprocess.call([
                "tar",
                "-xzvf",
                resource_path,
                f"--one-top-level={self._SLURM_TMP_RESOURCE}",
            ])
        except subprocess.CalledProcessError as e:
            logger.error(f"Error untaring slurm bins - {e}")

        # Wait on the existence of slurmd bin to verify that the untaring
        # of the slurm.tar.gz resource has completed before moving on.
        while not Path(f"{self._SLURM_TMP_RESOURCE}/sbin/slurmd").exists():
            sleep(1)

        for slurm_resource_dir in ['bin', 'sbin', 'lib', 'include']:
            cmd = (
                f"cp -R {self._SLURM_TMP_RESOURCE}/{slurm_resource_dir}/* "
                f"/usr/local/{slurm_resource_dir}/"
            )
            try:
                subprocess.call(cmd, shell=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"Error provisioning fs - {e}")

    def _set_ld_library_path(self):
        """Set the LD_LIBRARY_PATH."""
        Path('/etc/ld.so.conf.d/slurm.conf').write_text("/usr/local/lib/slurm")
        try:
            subprocess.call(["ldconfig"])
        except subprocess.CalledProcessError as e:
            logger.error(f"Error setting LD_LIBRARY_PATH - {e}")

    def _setup_systemd(self):
        """Setup systemd services for slurm components."""
        try:
            subprocess.call([
                "cp",
                self._source_systemd_template,
                self._target_systemd_template
            ])
            subprocess.call([
                "systemctl",
                "daemon-reload",
            ])
            self.slurm_systemctl("enable")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error setting up systemd - {e}")
