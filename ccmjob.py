# Copyright (C) 2014 Linaro Limited
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

import atexit
import errno
import shutil
import tempfile
import datetime
import time
import pytz
import traceback
import os

import sys
import suds

from suds.client import Client


from lava_common.exceptions import (
    LAVABug,
    LAVAError,
    JobError,
)
from lava_common.utils import debian_package_version
from lava_common.ccmloghandler import CcmLoggerHandler
from lava_dispatcher.logical import PipelineContext
from lava_dispatcher.diagnostics import DiagnoseNetwork
from lava_dispatcher.protocols.multinode import MultinodeProtocol  # pylint: disable=unused-import
from lava_common.constants import DISPATCHER_DOWNLOAD_DIR

from lava_dispatcher.job import Job

wsdl_url = "http://128.224.179.178:9005/?wsdl"


class CCMJob(Job):  # pylint: disable=too-many-instance-attributes
    def __init__(self, job_id, parameters, logger):  # pylint: disable=too-many-arguments
        super(CCMJob, self).__init__(job_id, parameters, logger)
        self.logger.info("CCMJob inited")
        self.logger.addHandler(CcmLoggerHandler(job_id))

    def cleanup(self, connection):
        if self.cleaned:
            self.logger.info("Cleanup already called, skipping")
            return

        super().cleanup(connection)
        # add by ccm yujie.hao@windriver.com
        try:
            client = Client(wsdl_url)
            client.set_options(location=wsdl_url)
            result = client.service.end_test_report(self.job_id)
            self.logger.info("CCMJob Send ccm result trans server : %s", result)
        except:
            self.logger.info("[CCMJobi_cleanup] connect to trans server failed")

