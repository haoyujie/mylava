# Copyright (C) 2014,2015 Linaro Limited
#
# Author: Neil Williams <neil.williams@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

# List just the subclasses supported for this base strategy
# imported by the parser to populate the list of subclasses.

import imp
import os
import re
import shutil
import fileinput
import tempfile

from lava_dispatcher.action import (
    Action,
    Pipeline,
    JobError,
)

from lava_dispatcher.logical import Deployment
from lava_dispatcher.actions.deploy import DeployAction
from lava_dispatcher.actions.deploy.download import DownloaderAction
from lava_dispatcher.actions.deploy.apply_overlay import OverlayAction
from lava_dispatcher.actions.deploy.environment import DeployDeviceEnvironment
from lava_dispatcher.utils.network import dispatcher_ip
from lava_extra.target_utils import (
    Vlm,
    Utils,
)

class VlmDeploy(Deployment):
    """
    Strategy class for a vlm ramdisk based Deployment.
    Downloads the relevant parts, copies to the vlm location.
    Limited to ipxe boot ramdisk or nfsrootfs.
    rootfs deployments would format the device and create a single partition for the rootfs.
    """

    compatibility = 1
    name = 'vlm'

    def __init__(self, parent, parameters):
        super().__init__(parent)
        self.action = VlmAction()
        self.action.section = self.action_type
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if 'to' not in parameters:
            return False, '"to" is not in deploy parameters'
        if parameters['to'] != 'vlm':
            return False, '"to" parameter is not "vlm"'
        if 'vlm' in device['actions']['deploy']['methods']:
            return True, 'accepted'
        return False, '"vlm" was not in the device configuration deploy methods"'

class VlmDeployAction(Action):
    """
    Base class for all vlm deploy actions which copy images (kernel,rootfs,dtb)
    to lab server. Extra copy pxeconfig to lab server and boot target.
    """  
    name = "vlm-deploy"
    description = "deploy images to server or media"
    summary = "copy image to server or media"

    def __init__(self):
        super().__init__()
        self.params = None
        self.device_barcode = None
        self.device_type = None
        self.device_name = None
        self.rootfs_path = None
        self.vlmtool = Vlm()
        self.utils = Utils()

    def get_device_info(self):
        self.device_type = self.job.parameters.get('device_type')
        self.device_name = self.job.device.get('hostname')
        self.device_barcode = self.utils.target_name_to_barcode(self.device_name)
        if not self.vlmtool.reserve_target(self.device_barcode):
            error_message = "Reserve target (%s : %s) failed" % (self.device_name, self.device_barcode)
            raise JobError(error_message)

    def write_pxeconfig(self):
        """
        Replace any existing pxeconfig with a master copy.
        Master pxeconfig files are just pxeconfig files which are known to work.
        They will be used to boot the target with a master image and will also
        be used as the basis for the pxeconfig files used by testcases
        """
        src_pxeconfig_filename = "%s/%s/gpxeboot.cfg" % (self.utils.MASTER_PXECONFIG_DIR, self.device_name)
        dst_pxeconfig_filename = "%s/%s/pxeboot.cfg" % (self.utils.TUXLAB_MOUNT, self.device_barcode)

        # Check that we have a master pxeconfig available for the target
        if not os.path.isfile(src_pxeconfig_filename):
            error_message = "File doesn't exist. (%s)" % src_pxeconfig_filename
            raise JobError(error_message)

        # Warn if the destination file doesn't exist. We don't expect to see this and
        # it doesn't mean we will fail but it is of interest if it happens.
        if not os.path.isfile(dst_pxeconfig_filename):
            self.logger.debug("Warning: Destination file doesn't exist. (%s)" % dst_pxeconfig_filename)
        try:
            shutil.copy(src_pxeconfig_filename, dst_pxeconfig_filename)
        except IOError as e:
            error_message = "Could not copy pxeconfig file. (%s)" % e.strerror
            raise JobError(error_message)
        self.logger.debug("Successfully wrote pxeconfig to lab server.")

    def update_pxeconfig(self):
        keep_orignal_rootfs_dev = False
        boot_cmds = ' ip=dhcp selinux=0 enforcing=0'
        pxeconfig_filename = self.utils.target_pxeconfig_filename(barcode=self.device_barcode)
        self.logger.info("update_pxeconfig, pxeconfig_filename : %s", str(pxeconfig_filename))
        default_bootargs = 'ipv6.disable=1'
        if 'kernel' in self.parameters:
            if 'rootfs_path' not in self.parameters:
                keep_orignal_rootfs_dev = True
            else:
                self.rootfs_path = self.parameters.get('rootfs_path')
                boot_cmds += ' root='+self.rootfs_path+' rw'
        if 'bootargs' in self.parameters:
            boot_cmds += ' '+self.parameters.get('bootargs')
        else:
            boot_cmds += ' '+default_bootargs
        try:
            shutil.copy(pxeconfig_filename, 'gpxe_tmp.conf')
            for line in fileinput.input('gpxe_tmp.conf', inplace=True):
                # The commas at the end of these lines are intentional
                # avoids adding additional newlines in the file
                if line.find('Golden - LAVA') != -1:
                    print(line.replace('Golden - LAVA', 'Testrun - LAVA'),end=" ")
                elif line.find('+ initramfs') != -1:
                    print(line.replace('+ initramfs', 'rootfs on %s' % self.rootfs_path),end=" ")
                elif line.find('kernel') == 0 and line.find('tftp') != -1:
                    cut_point = line.find('ip=')
                    if keep_orignal_rootfs_dev : cut_point = -1
                    line = line[0:cut_point] + boot_cmds
                    self.logger.info("update_pxeconfig, line : %s", str(line))
                    print(line,end=" ")
                elif line.find('append') == 0:
                    cut_point = line.find('ip=')
                    if keep_orignal_rootfs_dev : cut_point = -1
                    line = line[0:cut_point] + boot_cmds
                    self.logger.info("update_pxeconfig, line : %s", str(line))
                    print(line,end=" ")
                elif line.find('Append') == 0:
                    cut_point = line.find('ip=')
                    if keep_orignal_rootfs_dev : cut_point = -1
                    line = line[0:cut_point] + boot_cmds
                    self.logger.info("update_pxeconfig, line : %s", str(line))
                    print(line,end=" ")
                else:
                    print(line,end=" ")
            shutil.copy('gpxe_tmp.conf',pxeconfig_filename)
        except:
            raise JobError("Deployment failed, miss pxeconfig")

class VlmDeployMasterAction(VlmDeployAction):
    """
    Runs vlmtool copyfile master kernel image to deploy ramdisk image and boot
    """
    name = "deploy-master-image"
    description = "deploy ramdisk image to lab server"
    summary = "copy ramdisk image via vlmtool"

    def __init__(self):
        super().__init__()

    def validate(self):
        super().validate()
        if 'master' not in self.parameters:
            if 'kernel' not in self.parameters:
                self.errors = "missing master/kernel image to deployment."
            elif 'rootfs' not in self.parameters:
                self.errors = "missing rootfs image to deployment, kernel exist."
        # No need to go further if an error was already detected
        if not self.valid:
            return

    def deploy_image(self):
        """
        Deploy master image, in order to deploy test images in hard disk as boot media.
        """
        self.logger.debug("Copy files using VLM tool")
        # TODO get this working such that we can respect master_nfsrootfs to allow booting a
        # master image with a rootfs. For now we just handle a kernel with an initramfs.

        kernel_tmp = None
        dtb_tmp    = None
        rootfs_tmp = None

        if self.device_type == 'x86_vlm':
            self.write_pxeconfig()
        if 'dtb' in self.parameters:
            dtb_tmp = self.parameters.get('dtb')
            #dtb_tmp = re.findall('artifacts(.*)$', dtb_url)[0]
        if 'kernel' in self.parameters:
            kernel_tmp = self.parameters.get('kernel')
            #kernel_tmp = re.findall('artifacts(.*)$', kernel_url)[0]

            rootfs_tmp = self.parameters.get('rootfs').get('url')
            #rootfs_tmp = re.findall('artifacts(.*)$', rootfs_url)[0]
            self.logger.debug("kernel: " + kernel_tmp)
            self.logger.debug("rootfs: " + rootfs_tmp)
        elif 'master' in self.parameters:
            kernel_tmp = self.parameters.get('master')
            if self.device_type == 'x86_vlm':
                self.update_pxeconfig()
        if not self.vlmtool.copy_files(self.device_barcode, kernel=kernel_tmp, rootfs=rootfs_tmp, dtb = dtb_tmp):
            if self.vlmtool.error_str:
                self.logger.error(self.vlmtool.error_str)
                return False
        return True

    def add_boot_actions(self):
        """
        After master or kernel&rootfs images deployed, startup board and want to deploy
        testing images on hard disk (sata, sd or usb). 
        Here add boot actions(connect-device, reset-device, auto-login-action)
        """
        index = 0
        boot_action_names  = ['minimal-boot','uboot-action']
        boot_action_names += ['uboot-from-media','bootloader-overlay','connect-device','uboot-retry','reset-device','auto-login-action']
        boot_action = None
        parent_action = None
        for jobact in self.job.pipeline.actions:
            if jobact.name in boot_action_names:
                boot_action = jobact
            if jobact.pipeline is not None and self in jobact.pipeline.actions:
                index = jobact.pipeline.actions.index(self) 
                parent_action = jobact
        for act in boot_action.pipeline.actions:
            if act.name in boot_action_names:
                index += 1
                parent_action.pipeline.actions.insert(index, act)

    def run(self, connection, max_end_time):  # pylint: disable=too-many-locals
        """
        Retrieve the decompressed image from the dispatcher by calling the tool specified
        by the test writer, from within the test image of the first deployment, using the
        device to write directly to the secondary media, without needing to cache on the device.
        """
        connection = super().run(connection, max_end_time)

        self.get_device_info()
        if self.deploy_image() is not True:
            self.logger.error("Deploy master image failed.")

        if 'rootfs_path' in self.parameters:
            self.add_boot_actions()

        return connection

class VlmDeploySlaveAction(VlmDeployAction):
    """
    Runs vlmtool copyfile master kernel image to deploy ramdisk image and boot
    """
    name = "deploy-slave-image"
    description = "deploy image to server or media"
    summary = "copy images to server or media"

    def __init__(self):
        super().__init__()

    def validate(self):
        super().validate()
        if 'rootfs_path' not in self.parameters:
            self.errors = "missing rootfs_path to deployment"
        # No need to go further if an error was already detected
        if not self.valid:
            return

    def deploy_image(self):
        """
        Deploy slave images in hard disk as boot media.
        """
        self.logger.debug("Deploy rootfs to harddisk.")
        # TODO get this working such that we can respect master_nfsrootfs to allow booting a
        # master image with a rootfs. For now we just handle a kernel with an initramfs.
        if self.device_type == 'x86_vlm':
            self.update_pxeconfig()
        return True

    def target_extract(self, connection, dest):
        """
        Retrieve the decompressed image from the dispatcher by calling the tool specified
        by the test writer, from within the test image of the first deployment, using the
        device to write directly to the secondary media, without needing to cache on the device.
        """
        d_file = self.get_namespace_data(action='download-action', label='rootfs', key='file')
        if not d_file:
            self.logger.debug("Skipping %s - nothing downloaded")
            return connection
        decompressed_image = os.path.basename(d_file)

        decompression_char = ''
        if decompressed_image.endswith('.gz') or decompressed_image.endswith('.tgz'):
            decompression_char = 'z'
        elif decompressed_image.endswith('.bz2'):
            decompression_char = 'j'
        elif decompressed_image.endswith('.tar'):
            decompression_char = ''
        else:
            raise JobError('bad file extension: %s' % tar_url)

        storage_suffix = self.get_namespace_data(action='vlm-deploy', label='storage', key='suffix')
        if not storage_suffix:
            storage_suffix = ''
        suffix = "%s/%s" % ("tmp", storage_suffix)
        self.logger.debug("suffix :  %s", suffix)

        # As the test writer can use any tool we cannot predict where the
        # download URL will be positioned in the download command.
        # Providing the download URL as a substitution option gets round this
        ip_addr = dispatcher_ip(self.job.parameters['dispatcher'])
        download_url = "http://%s/%s/%s" % (
            ip_addr, suffix, decompressed_image
        )

        download_cmd = 'ping 8.8.8.8 -c 4 ;' +\
            'wget --no-check-certificate --no-proxy ' +\
            '--connect-timeout=30 -S --progress=dot -e dotbytes=2M ' +\
            '-O- %s' % (download_url)
        extract_cmd = 'tar --warning=no-timestamp --numeric-owner -C %s -x%sf -' % (dest, decompression_char)

        prompt_string = connection.prompt_str
        connection.prompt_str = 'written to stdout'
        self.logger.debug("Changing prompt to %s", connection.prompt_str)
        connection.sendline("%s | %s" % (download_cmd, extract_cmd))
        self.wait(connection)
        if not self.valid:
            self.logger.error(self.errors)

        connection.prompt_str = prompt_string
        self.logger.debug("Changing prompt to %s", connection.prompt_str)
        self.set_namespace_data(action='shared', label='shared', key='connection', value=connection)
        return connection

    def prepare_testpartition(self, connection, fstype):
        force = "-F"
        self.logger.info("Format testrootfs partition")
        if fstype.startswith("ext"):
            force = "-F"
        elif fstype == "btrfs":
            force = "-f"

        umount_cmd = 'nice umount -f %s && uname' % self.rootfs_path
        prompt_string = connection.prompt_str
        connection.prompt_str = ["Linux","not mounted"]
        self.logger.debug("Changing prompt to %s", connection.prompt_str)
        connection.sendline("%s" % umount_cmd)
        self.wait(connection)
        if not self.valid:
            self.logger.error(self.errors)

        mkfs_cmd = 'nice mkfs %s -t %s -q %s -L %s && uname' \
                   % (force, fstype, self.rootfs_path, self.rootfs_path.split('/')[2])
        connection.sendline("%s" % mkfs_cmd)
        self.wait(connection)
        if not self.valid:
            self.logger.error(self.errors)

        mount_cmd = 'nice mount %s /mnt && uname' % (self.rootfs_path)
        connection.sendline("%s" % mount_cmd)
        self.wait(connection)
        if not self.valid:
            self.logger.error(self.errors)

        try:
            self.target_extract(connection,'/mnt/')
        finally:
            connection.sendline("%s" % umount_cmd)
            self.wait(connection)
            if not self.valid:
                self.logger.error(self.errors)

        connection.prompt_str = prompt_string
        self.logger.debug("Changing prompt to %s", connection.prompt_str)
        self.set_namespace_data(action='shared', label='shared', key='connection', value=connection)
        return connection

    def run(self, connection, max_end_time):  # pylint: disable=too-many-locals
        """
        Retrieve the decompressed image from the dispatcher by calling the tool specified
        by the test writer, from within the test image of the first deployment, using the
        device to write directly to the secondary media, without needing to cache on the device.
        """
        connection = super().run(connection, max_end_time)
        self.get_device_info()
        if self.deploy_image() is not True:
            self.logger.error("Deploy rootfs image failed.")
        if 'rootfs_path' in self.parameters:
            self.rootfs_path = self.parameters.get('rootfs_path')
            self.prepare_testpartition(connection,'ext4')
        return connection

class VlmAction(DeployAction):  # pylint:disable=too-many-instance-attributes

    name = "vlm-deploy"
    description = "download images and deploy using vlmtool"
    summary = "vlm deployment"

    def __init__(self):
        super().__init__()
        self.suffix = None
        self.image_path = None

    def validate(self):
        super().validate()
        # No need to go further if an error was already detected
        if 'kernel' in self.parameters:
            if 'rootfs' not in self.parameters:
                self.errors = "kernel image exist, No rootfs image"
        elif 'master' not in self.parameters:
                self.errors = "master/kernel images all not exist"

        if not self.valid:
            return

        # Extract the 3 last path elements. See action.mkdtemp()
        suffix = os.path.join(*self.image_path.split('/')[-2:])
        self.set_namespace_data(action=self.name, label='storage', key='suffix', value=suffix)

        if 'rootfs' in self.parameters:
            suffix = os.path.join(*self.image_path.split('/')[-2:])
            suffix = os.path.join(suffix, "rootfs")
            self.set_namespace_data(action=self.name, label='storage', key='suffix', value=suffix)

    def populate(self, parameters):
        """
        The dispatcher does the first download as the first deployment is not guaranteed to
        have DNS resolution fully working, so we can use the IP address of the dispatcher
        to get it (with the advantage that the dispatcher decompresses it so that the ramdisk
        can pipe the raw image directly from wget to deploy with tools (dd,tar and so on).)
        This also allows the use of local file:// locations which are visible to the dispatcher
        but not the device.
        """
        self.image_path = self.mkdtemp()
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)

        uniquify = parameters.get('uniquify', True)
        self.internal_pipeline.add_action(VlmDeployMasterAction())

        if 'rootfs_path' in parameters:
            self.internal_pipeline.add_action(DownloaderAction('rootfs', path=self.image_path, uniquify=uniquify))
            self.internal_pipeline.add_action(VlmDeploySlaveAction())

        if self.test_needs_overlay(parameters):
            self.internal_pipeline.add_action(OverlayAction())  # idempotent, includes testdef
        
        # FIXME: could support tarballs too
        if self.test_needs_deployment(parameters):
            self.internal_pipeline.add_action(DeployDeviceEnvironment())

        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self = %s ", str(self) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx dir(self) = %s ", dir(self) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx str(self.pipeline) = %s ", str(self.pipeline) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx str(self.pipeline.actions) = %s ", str(self.pipeline.actions) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.job = %s ", str(self.job) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx dir(self.job) = %s ", dir(self.job) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx str(self.job.pipeline) = %s ", str(self.job.pipeline) )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx parameters: %s", parameters )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.parameters: %s", self.parameters )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.job.parameters: %s", self.job.parameters )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.job.parameters['actions'][0]: %s", self.job.parameters['actions'][0] )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.job.parameters['actions'][1]: %s", self.job.parameters['actions'][1] )
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.job.device: %s", self.job.device)
        #self.logger.debug("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx self.job.device.target : %s", self.job.device.target )
