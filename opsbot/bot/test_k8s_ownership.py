#!/usr/bin/env python3
"""Kubernetes mutation calls fail closed after Opsbot fencing."""

import unittest
import sys
import types
from unittest.mock import patch

# Keep this contract test runnable without installing the production image's
# Kubernetes dependency locally.
if "kubernetes" not in sys.modules:
    kubernetes = types.ModuleType("kubernetes")
    kubernetes.client = types.ModuleType("kubernetes.client")
    kubernetes.client.rest = types.ModuleType("kubernetes.client.rest")
    kubernetes.client.rest.ApiException = type("ApiException", (Exception,), {})
    kubernetes.client.AppsV1Api = type("AppsV1Api", (), {})
    kubernetes.config = types.ModuleType("kubernetes.config")
    kubernetes.stream = types.ModuleType("kubernetes.stream")
    kubernetes.stream.stream = lambda *args, **kwargs: None
    sys.modules.update({
        "kubernetes": kubernetes,
        "kubernetes.client": kubernetes.client,
        "kubernetes.client.rest": kubernetes.client.rest,
        "kubernetes.config": kubernetes.config,
        "kubernetes.stream": kubernetes.stream,
    })

import k8s_ops
from ownership import OwnershipError


class K8sOwnershipTests(unittest.TestCase):
    def tearDown(self):
        k8s_ops.set_authority_checker(lambda: None)

    def test_restart_checks_ownership_before_patch_client_is_created(self):
        def fenced():
            raise OwnershipError("fenced")

        k8s_ops.set_authority_checker(fenced)
        with patch.object(k8s_ops.client, "AppsV1Api") as api:
            with self.assertRaises(OwnershipError):
                k8s_ops.restart_deployment("jmusicbot", "jmusicbot")
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
