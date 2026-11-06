# Keep the stable install path so an already-running shell survives upgrades.
set -g __splash_completion_source (builtin realpath -s -- (status filename))

function __splash_models
    # The helper beside the script's current target, found again each time.
    set -l helper (path dirname -- (path resolve -- $__splash_completion_source))/models
    test -x $helper
    and $helper
end

# As in Bash and Zsh: only the value of --model, and only after `splash serve`.
function __splash_model_value
    set -l words (commandline -opc)
    test "$words[2]" = serve
    and not string match -qr -- '^-[^=]*$' (commandline -ct)
end

function __splash_local_file_value
    set -l words (commandline -opc)
    test "$words[2]" = serve; or return 1
    contains -- -- $words[3..-1]; and return 1
    contains -- "$words[-1]" --model-dir --draft-model --mmproj
    or string match -qr -- '^--(model-dir|draft-model|mmproj)=' (commandline -ct)
end

for cmd in splash splaude splash-claude
    complete -c $cmd -f
    complete -c $cmd -n 'test (count (commandline -opc)) -eq 1' -a 'serve setup claude codex opencode hermes pi'
    complete -c $cmd -n __splash_model_value -l model -x -a '(__splash_models)'
    complete -c $cmd -n __splash_local_file_value -F
end
