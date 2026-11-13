/**
 * @name Successfully extracted compiler inputs
 * @description Lists source files that the Java/Kotlin extractor processed without errors.
 * @kind table
 * @id collie/compiled-files
 */
import java

from Compilation compilation, int index, File file
where
  file = compilation.getFileCompiled(index) and
  compilation.extractionSuccessful() and
  compilation.fileCompiledSuccessful(index) and
  file.isSourceFile()
select file.getRelativePath()
