# Copyright 2016 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os
import unittest
import xml.etree.ElementTree as ET
from textwrap import dedent

from pants.base.build_environment import get_buildroot
from pants.util.collections import assert_single_element
from pants.util.contextutil import open_zip, temporary_dir
from pants.util.dirutil import safe_open

SHAPELESS_CLSFILE = "org/pantsbuild/testproject/unicode/shapeless/ShapelessExample.class"
SHAPELESS_TARGET = "testprojects/src/scala/org/pantsbuild/testproject/unicode/shapeless"


class BaseZincCompileIntegrationTest:
    def create_file(self, path, value):
        with safe_open(path, "w") as f:
            f.write(value)

    def run_run(self, target_spec, config, workdir):
        args = ["run", target_spec]
        pants_run = self.run_pants_with_workdir(args, workdir, config)
        self.assert_success(pants_run)

    def test_scala_compile_jar(self):
        jar_suffix = "z.jar"
        with self.do_test_compile(SHAPELESS_TARGET, expected_files=[jar_suffix]) as found:
            with open_zip(self.get_only(found, jar_suffix), "r") as jar:
                self.assertTrue(
                    jar.getinfo(SHAPELESS_CLSFILE), "Expected a jar containing the expected class."
                )

    # TODO: this could be converted into a unit test!
    def test_consecutive_compiler_option_sets(self):
        """Test that the ordering of args in compiler option sets are respected.

        Generating a scalac profile requires two consecutive arguments, '-Yprofile-destination' and
        its following argument, the file to write the CSV profile to. We want to be able to allow
        users to successfully run scalac with profiling from pants, so we test this case in
        particular. See the discussion from https://github.com/pantsbuild/pants/pull/7683.
        """
        with temporary_dir() as tmp_dir:
            profile_destination = os.path.join(tmp_dir, "scala_profile.csv")
            self.do_command(
                "compile",
                SHAPELESS_TARGET,
                # Flags to enable profiling and statistics on target
                config={
                    "compile.rsc": {
                        "default_compiler_option_sets": ["profile"],
                        "compiler_option_sets_enabled_args": {
                            "profile": [
                                "-S-Ystatistics",
                                "-S-Yhot-statistics-enabled",
                                "-S-Yprofile-enabled",
                                "-S-Yprofile-destination",
                                f"-S{profile_destination}",
                                "-S-Ycache-plugin-class-loader:last-modified",
                            ],
                        },
                    },
                },
            )
            self.assertTrue(os.path.isfile(profile_destination))

    def test_scala_empty_compile(self):
        with self.do_test_compile(
            "testprojects/src/scala/org/pantsbuild/testproject/emptyscala", expected_files=[]
        ):
            # no classes generated by this target
            pass

    def test_scala_shared_sources(self):
        clsname = "SharedSources.class"

        with self.do_test_compile(
            "testprojects/src/scala/org/pantsbuild/testproject/sharedsources::",
            expected_files=[clsname],
        ) as found:
            classes = found[clsname]
            self.assertEqual(2, len(classes))
            for cls in classes:
                self.assertTrue(
                    cls.endswith("org/pantsbuild/testproject/sharedsources/SharedSources.class")
                )

    def test_scala_failure(self):
        """With no initial analysis, a failed compilation shouldn't leave anything behind."""
        analysis_file = (
            "testprojects.src.scala."
            "org.pantsbuild.testproject.compilation_failure.compilation_failure.analysis"
        )
        with self.do_test_compile(
            "testprojects/src/scala/org/pantsbuild/testprojects/compilation_failure",
            expected_files=[analysis_file],
            expect_failure=True,
        ) as found:
            self.assertEqual(0, len(found[analysis_file]))

    def test_scala_with_java_sources_compile(self):
        with self.do_test_compile(
            "testprojects/src/scala/org/pantsbuild/testproject/javasources",
            expected_files=["ScalaWithJavaSources.class", "JavaSource.class"],
        ) as found:

            self.assertTrue(
                self.get_only(found, "ScalaWithJavaSources.class").endswith(
                    "org/pantsbuild/testproject/javasources/ScalaWithJavaSources.class"
                )
            )

            self.assertTrue(
                self.get_only(found, "JavaSource.class").endswith(
                    "org/pantsbuild/testproject/javasources/JavaSource.class"
                )
            )

    def test_apt_compile(self):
        with self.do_test_compile(
            "testprojects/src/java/org/pantsbuild/testproject/annotation/processor",
            expected_files=[
                "ResourceMappingProcessor.class",
                "javax.annotation.processing.Processor",
            ],
        ) as found:

            self.assertTrue(
                self.get_only(found, "ResourceMappingProcessor.class").endswith(
                    "org/pantsbuild/testproject/annotation/processor/ResourceMappingProcessor.class"
                )
            )

            # processor info file under classes/ dir
            processor_service_files = list(
                filter(lambda x: "classes" in x, found["javax.annotation.processing.Processor"])
            )
            # There should be only a per-target service info file.
            self.assertEqual(1, len(processor_service_files))
            processor_service_file = list(processor_service_files)[0]
            self.assertTrue(
                processor_service_file.endswith(
                    "META-INF/services/javax.annotation.processing.Processor"
                )
            )
            with open(processor_service_file, "r") as fp:
                self.assertEqual(
                    "org.pantsbuild.testproject.annotation.processor.ResourceMappingProcessor",
                    fp.read().strip(),
                )

    def test_apt_compile_and_run(self):
        with self.do_test_compile(
            "testprojects/src/java/org/pantsbuild/testproject/annotation/main",
            expected_files=["Main.class", "deprecation_report.txt"],
        ) as found:

            self.assertTrue(
                self.get_only(found, "Main.class").endswith(
                    "org/pantsbuild/testproject/annotation/main/Main.class"
                )
            )

            # This is the proof that the ResourceMappingProcessor annotation processor was compiled in a
            # round and then the Main was compiled in a later round with the annotation processor and its
            # service info file from on its compile classpath.
            with open(self.get_only(found, "deprecation_report.txt"), "r") as fp:
                self.assertIn(
                    "org.pantsbuild.testproject.annotation.main.Main", fp.read().splitlines()
                )

    def test_stale_apt_with_deps(self):
        """An annotation processor with a dependency doesn't pollute other annotation processors.

        At one point, when you added an annotation processor, it stayed configured for all
        subsequent compiles.  Meaning that if that annotation processor had a dep that wasn't on the
        classpath, subsequent compiles would fail with missing symbols required by the stale
        annotation processor.
        """

        # Demonstrate that the annotation processor is working
        with self.do_test_compile(
            "testprojects/src/java/org/pantsbuild/testproject/annotation/processorwithdep/main",
            expected_files=["Main.class", "Main_HelloWorld.class", "Main_HelloWorld.java"],
        ) as found:
            gen_file = self.get_only(found, "Main_HelloWorld.java")
            self.assertTrue(
                gen_file.endswith(
                    "org/pantsbuild/testproject/annotation/processorwithdep/main/Main_HelloWorld.java"
                ),
                msg=f"{gen_file} does not match",
            )

        # Try to reproduce second compile that fails with missing symbol
        with self.temporary_workdir() as workdir:
            with self.temporary_cachedir() as cachedir:
                # This annotation processor has a unique external dependency
                self.assert_success(
                    self.run_test_compile(
                        workdir,
                        cachedir,
                        "testprojects/src/java/org/pantsbuild/testproject/annotation/processorwithdep::",
                    )
                )

                # When we run a second compile with annotation processors, make sure the previous annotation
                # processor doesn't stick around to spoil the compile
                self.assert_success(
                    self.run_test_compile(
                        workdir,
                        cachedir,
                        "testprojects/src/java/org/pantsbuild/testproject/annotation/processor::",
                        clean_all=False,
                    )
                )

    def test_scalac_plugin_compile(self):
        with self.do_test_compile(
            "examples/src/scala/org/pantsbuild/example/scalac/plugin:other_simple_scalac_plugin",
            expected_files=["OtherSimpleScalacPlugin.class", "scalac-plugin.xml"],
        ) as found:

            self.assertTrue(
                self.get_only(found, "OtherSimpleScalacPlugin.class").endswith(
                    "org/pantsbuild/example/scalac/plugin/OtherSimpleScalacPlugin.class"
                )
            )

            # Grab only the files under classes/ dir
            scalac_xml_under_classes_dir = list(
                filter(lambda x: "classes" in x, found["scalac-plugin.xml"])
            )
            self.assertEqual(1, len(scalac_xml_under_classes_dir))

            # Ensure that the plugin registration file is written to the root of the classpath.
            path = scalac_xml_under_classes_dir[0]
            self.assertTrue(
                path.endswith("/classes/scalac-plugin.xml"),
                "plugin registration file `{}` not located at the "
                "root of the classpath".format(path),
            )

            # And that it is well formed.
            root = ET.parse(path).getroot()
            self.assertEqual("plugin", root.tag)
            self.assertEqual("other_simple_scalac_plugin", root.find("name").text)
            self.assertEqual(
                "org.pantsbuild.example.scalac.plugin.OtherSimpleScalacPlugin",
                root.find("classname").text,
            )

    def test_scalac_debug_symbol(self):
        with self.do_test_compile(
            "examples/src/scala/org/pantsbuild/example/scalac/plugin:simple_scalac_plugin",
            expected_files=["SimpleScalacPlugin.class", "scalac-plugin.xml"],
            extra_args=["--compile-rsc-debug-symbols"],
        ):
            pass

    def test_zinc_unsupported_option(self):
        with self.temporary_workdir() as workdir:
            with self.temporary_cachedir() as cachedir:
                # compile with an unsupported flag
                pants_run = self.run_test_compile(
                    workdir,
                    cachedir,
                    "testprojects/src/scala/org/pantsbuild/testproject/emptyscala",
                    extra_args=[
                        "--compile-rsc-args=-recompile-all-fraction",
                        "--compile-rsc-args=0.5",
                    ],
                )
                self.assert_success(pants_run)

                # Confirm that we were warned.
                self.assertIn(
                    "is not supported, and is subject to change/removal", pants_run.stdout_data
                )

    def test_zinc_compiler_options_sets(self):
        def test_combination(target, expect_success, extra_args=[]):
            with self.temporary_workdir() as workdir:
                with self.temporary_cachedir() as cachedir:
                    pants_run = self.run_test_compile(
                        workdir,
                        cachedir,
                        "testprojects/src/scala/org/pantsbuild/testproject/compilation_warnings:{}".format(
                            target
                        ),
                        extra_args=extra_args,
                    )

                    if expect_success:
                        self.assert_success(pants_run)
                    else:
                        self.assert_failure(pants_run)

        test_combination("fatal", expect_success=False)
        test_combination("nonfatal", expect_success=True)

        test_combination(
            "fatal",
            expect_success=True,
            extra_args=[
                '--compile-rsc-compiler-option-sets-enabled-args={"fatal_warnings": ["-C-Werror"]}'
            ],
        )
        test_combination(
            "fatal",
            expect_success=False,
            extra_args=[
                '--compile-rsc-compiler-option-sets-disabled-args={"fatal_warnings": ["-S-Xfatal-warnings"]}'
            ],
        )

    def _compile_unused_import(self, use_barebones_logger=False):
        # Compile a target that we expect will raise an "Unused import" warning.
        with self.temporary_workdir() as workdir:
            with self.temporary_cachedir() as cachedir:
                args = ['--compile-rsc-args=+["-S-Ywarn-unused:_"]', "-ldebug",] + (
                    ["--compile-rsc-use-barebones-logger"] if use_barebones_logger else []
                )
                pants_run = self.run_test_compile(
                    workdir,
                    cachedir,
                    "testprojects/src/scala/org/pantsbuild/testproject/compilation_warnings/unused_import_warning:unused_import",
                    extra_args=args,
                )
                self.assert_success(pants_run)
                return pants_run

    def test_zinc_logs_warnings_properly(self):
        """Test that, with the standard logger, we log the warning in the expected format."""
        pants_run = self._compile_unused_import()
        # Confirm that we were warned in the expected format.
        expected_strings = [
            "/testprojects/src/scala/org/pantsbuild/testproject/compilation_warnings/unused_import_warning/UnusedImportWarning.scala:2:14: Unused import",
            "[warn] import scala.List // Unused import warning",
            "[warn] one warning found",
        ]

        for expected in expected_strings:
            self.assertIn(expected, pants_run.stdout_data)

    def _test_zinc_reports_diagnostic_counts(self, reporting):
        with self.temporary_workdir() as workdir:
            target = "testprojects/src/scala/org/pantsbuild/testproject/compilation_warnings/unused_import_warning:unused_import"
            with self.temporary_cachedir() as cachedir:
                args = ['--compile-rsc-args=+["-S-Ywarn-unused:_"]'] + (
                    ["--compile-rsc-report-diagnostic-counts"] if reporting else []
                )
                pants_run = self.run_test_compile(workdir, cachedir, target, extra_args=args,)
                self.assert_success(pants_run)

            expected_strings = [
                f"Reporting number of diagnostics for: {target}",
                "Error: 0",
                "Warning: 1",
                "Information: 0",
                "Hint: 0",
            ]

            for expected in expected_strings:
                if reporting:
                    self.assertIn(expected, pants_run.stdout_data)
                else:
                    self.assertNotIn(expected, pants_run.stdout_data)
            run_info_path = os.path.join(workdir, "run-tracker", "latest", "info")
            with open(run_info_path, "r") as run_info:

                def is_target_data_line(line):
                    return line.startswith("target_data: ")

                target_data_line = assert_single_element(filter(is_target_data_line, run_info))
                expected_target_data = (
                    "'diagnostic_counts': {'Error': 0, 'Warning': 1, 'Information': 0, 'Hint': 0}"
                )
                if reporting:
                    self.assertIn(expected_target_data, target_data_line)
                else:
                    self.assertNotIn(expected_target_data, target_data_line)

    def test_zinc_reports_diagnostic_counts_when_prompted(self):
        self._test_zinc_reports_diagnostic_counts(reporting=True)

    def test_zinc_does_not_report_diagnostic_counts_when_unprompted(self):
        self._test_zinc_reports_diagnostic_counts(reporting=False)

    def test_barebones_logger_works(self):
        """Test that the barebones logger logs the expected warning.

        TODO(#8312): this should be synced up with the normal logging output in order to use native-image zinc!
        """
        pants_run = self._compile_unused_import(use_barebones_logger=True)
        expected_strings = [
            "/testprojects/src/scala/org/pantsbuild/testproject/compilation_warnings/unused_import_warning/UnusedImportWarning.scala",
            "[warn] one warning found",
        ]

        for expected in expected_strings:
            self.assertIn(expected, pants_run.stdout_data)

    @unittest.expectedFailure
    def test_soft_excludes_at_compiletime(self):
        with self.do_test_compile(
            "testprojects/src/scala/org/pantsbuild/testproject/exclude_direct_dep",
            extra_args=["--resolve-ivy-soft-excludes"],
            expect_failure=True,
        ):
            # TODO See #4874. Should have failed to compile because its only dependency is excluded.
            pass

    def test_pool_created_for_fresh_compile_but_not_for_valid_compile(self):
        with self.temporary_cachedir() as cachedir, self.temporary_workdir() as workdir:
            # Populate the workdir.
            first_run = self.run_test_compile(
                workdir, cachedir, "testprojects/src/scala/org/pantsbuild/testproject/javasources"
            )

            self.assertIn("isolation-mixed-pool-bootstrap", first_run.stdout_data)

            # Run valid compile.
            second_run = self.run_test_compile(
                workdir, cachedir, "testprojects/src/scala/org/pantsbuild/testproject/javasources"
            )

            self.assertNotIn("isolation-mixed-pool-bootstrap", second_run.stdout_data)

    def test_source_compat_binary_incompat_scala_change(self):
        with temporary_dir() as cache_dir, self.temporary_workdir() as workdir, temporary_dir(
            root_dir=get_buildroot()
        ) as src_dir:

            config = {
                "cache.compile.rsc": {"write_to": [cache_dir], "read_from": [cache_dir]},
            }

            srcfile = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "A.scala")
            srcfile_b = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "B.scala")
            buildfile = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "BUILD")

            self.create_file(
                buildfile,
                dedent(
                    """
                    scala_library(name='a',
                                 sources=['A.scala'])
                    scala_library(name='b',
                                 sources=['B.scala'],
                                 dependencies=[':a'])
                    jvm_binary(name='bin',
                     main='org.pantsbuild.cachetest.B',
                     dependencies=[':b']
                    )
                    """
                ),
            )
            self.create_file(
                srcfile,
                dedent(
                    """
                    package org.pantsbuild.cachetest
                    object A {
                      def x(y: Option[Int] = None) = {
                        println("x");
                      }
                    }
                    """
                ),
            )

            self.create_file(
                srcfile_b,
                dedent(
                    """
                    package org.pantsbuild.cachetest
                    object B extends App {
                      A.x();
                      System.exit(0);
                    }
                    """
                ),
            )

            cachetest_bin_spec = os.path.join(
                os.path.basename(src_dir), "org", "pantsbuild", "cachetest:bin"
            )
            cachetest_spec = cachetest_bin_spec

            # Caches values A.class, B.class
            self.run_run(cachetest_spec, config, workdir)

            self.create_file(
                srcfile,
                dedent(
                    """
                    package org.pantsbuild.cachetest;
                    object A {
                      def x(y: Option[Int] = None, z:Option[Int]=None) = {
                        println("x");
                      }
                    }
                    """
                ),
            )
            self.run_run(cachetest_bin_spec, config, workdir)

    def test_source_compat_binary_incompat_java_change(self):
        with temporary_dir() as cache_dir, self.temporary_workdir() as workdir, temporary_dir(
            root_dir=get_buildroot()
        ) as src_dir:

            config = {
                "cache.compile.rsc": {"write_to": [cache_dir], "read_from": [cache_dir]},
                "compile.rsc": {"incremental_caching": True},
            }

            srcfile = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "A.java")
            srcfile_b = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "B.java")
            buildfile = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "BUILD")

            self.create_file(
                buildfile,
                dedent(
                    """
                    java_library(name='cachetest',
                                 sources=['A.java'])
                    java_library(name='b',
                                 sources=['B.java'],
                                 dependencies=[':a']
                                 )
                    jvm_binary(name='bin',
                        main='org.pantsbuild.cachetest.B',
                        dependencies=[':b']
                    )
                    """
                ),
            )
            self.create_file(
                srcfile,
                dedent(
                    """package org.pantsbuild.cachetest;
                    class A {
                      public static void x() {
                        System.out.println("x");
                      }
                    }
                    """
                ),
            )

            self.create_file(
                srcfile_b,
                dedent(
                    """package org.pantsbuild.cachetest;
                    class B {
                      public static void main(String[] args) {
                        A.x();
                      }
                    }
                    """
                ),
            )

            cachetest_spec = os.path.join(
                os.path.basename(src_dir), "org", "pantsbuild", "cachetest:cachetest"
            )

            self.run_run(cachetest_spec, config, workdir)

            self.create_file(
                srcfile,
                dedent(
                    """package org.pantsbuild.cachetest;
                    class A {
                      public static int x() {
                        System.out.println("x");
                        return 0;
                      }
                    }
                    """
                ),
            )

            self.run_run(cachetest_spec, config, workdir)

    def test_hermetic(self):
        extra_args = [
            "--compile-rsc-execution-strategy=hermetic",
            "--compile-rsc-incremental=False",
        ]

        with self.do_test_compile(
            "examples/src/scala/org/pantsbuild/example/hello/exe", extra_args=extra_args
        ):
            pass

    def test_differing_platforms(self):
        with temporary_dir() as cache_dir, self.temporary_workdir() as workdir, temporary_dir(
            root_dir=get_buildroot()
        ) as src_dir:

            config = {
                "cache.compile.rsc": {"write_to": [cache_dir], "read_from": [cache_dir]},
                "jvm-platform": {
                    "platforms": {
                        "six": {"source": "6", "target": "6"},
                        "seven": {"source": "7", "target": "7"},
                    },
                    "default_platform": "six",
                },
            }

            srcfile = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "A.java")
            buildfile = os.path.join(src_dir, "org", "pantsbuild", "cachetest", "BUILD")

            self.create_file(
                srcfile,
                dedent(
                    """
                    package org.pantsbuild.cachetest;
                    class A {
                      public static void main(String[] args) {
                        System.out.println("hello");
                      }
                    }
                    """
                ),
            )
            self.create_file(
                buildfile,
                dedent(
                    """
                    java_library(name='a',
                                 sources=['A.java'])
                    jvm_binary(name='bin',
                               main='org.pantsbuild.cachetest.A',
                               dependencies=[':a'])
                    """
                ),
            )

            cachetest_bin_spec = os.path.join(
                os.path.basename(src_dir), "org", "pantsbuild", "cachetest:bin"
            )
            cachetest_spec = cachetest_bin_spec

            # Cache values A.class
            self.run_run(cachetest_spec, config, workdir)

            self.create_file(
                buildfile,
                dedent(
                    """
                    java_library(name='a',
                                 platform='seven',
                                 sources=['A.java'])
                    jvm_binary(name='bin',
                               main='org.pantsbuild.cachetest.A',
                               platform='seven',
                               dependencies=[':a'])
                    """
                ),
            )
            self.run_run(cachetest_bin_spec, config, workdir)
